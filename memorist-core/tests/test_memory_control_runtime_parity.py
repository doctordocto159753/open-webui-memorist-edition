from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from fastapi import HTTPException

from memcore.api import routes_memory_control
from memcore.api.routes_memory_control import AttachmentActionRequest
from memcore.config import Settings
from memcore.imports.runtime import initialize_runtime_storage
from memcore.memory_control.policy import normalize_turn_policy
from memcore.memory_control.repository import MemoryControlRepository, ResolvedTurnPolicy
from memcore.memory_control.runtime import memory_control_connection
from memcore.models import utc_now


def _settings(runtime: str, tmp_path: Path) -> Settings:
    if runtime == "lite":
        return Settings(
            env="test",
            allow_legacy_actor_headers_for_tests=True,
            runtime_profile="lite",
            canonical_store="sqlite",
            db_path=str(tmp_path / "parity.sqlite3"),
            object_store_path=str(tmp_path / "objects-lite"),
        )
    dsn = os.getenv("MEMORIST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("requires real PostgreSQL")
    return Settings(
        env="test",
        allow_legacy_actor_headers_for_tests=True,
        runtime_profile="full",
        canonical_store="postgres",
        postgres_dsn=dsn,
        db_path=str(tmp_path / "must-not-exist.sqlite3"),
        object_store_path=str(tmp_path / "objects-full"),
        allow_full_graph_degraded=True,
        hot_scheduler="in_memory",
    )


@contextmanager
def _connection(settings: Settings) -> Iterator[Any]:
    initialize_runtime_storage(settings)
    with memory_control_connection(settings) as connection:
        yield connection


@pytest.mark.parametrize("runtime", ["lite", "full"], ids=["lite", "full_postgres"])
def test_policy_attachment_and_restart_semantics_are_identical(
    runtime: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(runtime, tmp_path)
    suffix = uuid4().hex
    workspace = f"workspace-{suffix}"
    project = f"project-{suffix}"
    session = f"session-{suffix}"
    message = f"message-{suffix}"
    attachment = f"attachment-{suffix}"
    cancelled_attachment = f"cancelled-attachment-{suffix}"
    user = f"user-{suffix}"
    chat = f"chat-{suffix}"
    now = utc_now()
    with _connection(settings) as connection:
        repository = MemoryControlRepository(connection, settings.runtime_profile)
        repository.set_default(
            scope_type="user",
            scope_uuid=user,
            workspace_uuid=workspace,
            turn_policy="no_recall",
            attachment_review=False,
        )
        repository.set_default(
            scope_type="chat",
            scope_uuid=chat,
            workspace_uuid=workspace,
            turn_policy="full",
            attachment_review=True,
        )
        resolved = repository.resolve_policy(
            system_default="private",
            workspace_uuid=workspace,
            user_uuid=user,
            chat_uuid=chat,
            turn_override=None,
        )
        assert resolved.policy.mode.value == "full"
        assert resolved.source == "chat"
        assert resolved.attachment_review is True
        connection.execute(
            "INSERT INTO workspaces (workspace_uuid, name, created_at, updated_at) "
            "VALUES (?, ?, ?, ?)",
            (workspace, suffix, now, now),
        )
        connection.execute(
            "INSERT INTO projects (project_uuid, workspace_uuid, name, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (project, workspace, suffix, now, now),
        )
        connection.execute(
            """
            INSERT INTO sessions (
                session_uuid, workspace_uuid, project_uuid, status, created_at, updated_at
            ) VALUES (?, ?, ?, 'active', ?, ?)
            """,
            (session, workspace, project, now, now),
        )
        connection.execute(
            """
            INSERT INTO messages (
                message_uuid, session_uuid, turn_index, role, creator_type, raw_text,
                processing_status, visibility, is_deleted, created_at, updated_at
            ) VALUES (?, ?, 0, 'user', 'user', 'parity prompt', 'pending',
                      'visible', false, ?, ?)
            """,
            (message, session, now, now),
        )
        repository.record_turn_contract(
            input_message_uuid=message,
            session_uuid=session,
            workspace_uuid=workspace,
            user_uuid=user,
            chat_uuid=chat,
            resolved=ResolvedTurnPolicy(
                policy=normalize_turn_policy("full"),
                source="chat",
                attachment_review=True,
            ),
        )
        connection.execute(
            """
            INSERT INTO memory_context_attachments (
                attachment_uuid, session_uuid, project_uuid, input_message_uuid,
                attachment_mode, ijson_attachment, rendered_attachment, token_count,
                owner_user_uuid, workspace_uuid, lifecycle_status, attachment_review,
                generation, created_at, schema_version
            ) VALUES (?, ?, ?, ?, 'full', '{}', 'bounded', 1, ?, ?, 'prepared',
                      true, 1, ?, 1)
            """,
            (attachment, session, project, message, user, workspace, now),
        )
        connection.execute(
            """
            INSERT INTO memory_context_attachments (
                attachment_uuid, session_uuid, project_uuid, input_message_uuid,
                attachment_mode, ijson_attachment, rendered_attachment, token_count,
                owner_user_uuid, workspace_uuid, lifecycle_status, attachment_review,
                generation, created_at, schema_version
            ) VALUES (?, ?, ?, ?, 'full', '{}', 'bounded', 1, ?, ?, 'prepared',
                      false, 1, ?, 1)
            """,
            (cancelled_attachment, session, project, message, user, workspace, now),
        )
        connection.commit()
        assert (
            repository.attachment_for_actor(
                attachment, user_uuid="attacker", workspace_uuid=workspace
            )
            is None
        )
        with pytest.raises(ValueError, match="approval required"):
            repository.transition_attachment(
                attachment,
                "delivered",
                user_uuid=user,
                workspace_uuid=workspace,
                idempotency_key=f"premature-delivery-{suffix}",
            )
        approved = repository.transition_attachment(
            attachment,
            "approved",
            user_uuid=user,
            workspace_uuid=workspace,
            idempotency_key=f"approve-{suffix}",
        )
        duplicate = repository.transition_attachment(
            attachment,
            "approved",
            user_uuid=user,
            workspace_uuid=workspace,
            idempotency_key=f"approve-{suffix}",
        )
        assert duplicate["lifecycle_event_uuid"] == approved["lifecycle_event_uuid"]
        connection.execute(
            "UPDATE memory_context_attachments SET stale_reason = 'newer-generation' "
            "WHERE attachment_uuid = ?",
            (attachment,),
        )
        connection.commit()
        with pytest.raises(ValueError, match="stale attachment"):
            repository.transition_attachment(
                attachment,
                "delivered",
                user_uuid=user,
                workspace_uuid=workspace,
                idempotency_key=f"stale-delivery-{suffix}",
            )
        cancelled = repository.transition_attachment(
            cancelled_attachment,
            "cancelled_before_send",
            user_uuid=user,
            workspace_uuid=workspace,
            idempotency_key=f"cancel-{suffix}",
        )
        cancelled_duplicate = repository.transition_attachment(
            cancelled_attachment,
            "cancelled_before_send",
            user_uuid=user,
            workspace_uuid=workspace,
            idempotency_key=f"cancel-{suffix}",
        )
        assert cancelled_duplicate["lifecycle_event_uuid"] == cancelled["lifecycle_event_uuid"]
        contract = repository.get_turn_contract(message)
        assert contract is not None
        assert contract["turn_policy"] == "full"
        repository.audit(
            "assistant_capture",
            normalize_turn_policy("full"),
            session_uuid=session,
            input_message_uuid=message,
            workspace_uuid=workspace,
            user_uuid=user,
            attachment_uuid=None,
            detail={"review_disposition": "cancelled_before_send"},
        )
        assistant_audit = connection.execute(
            "SELECT turn_policy, attachment_uuid FROM memorist_policy_audit_events "
            "WHERE event_type = 'assistant_capture' AND input_message_uuid = ? "
            "ORDER BY created_at DESC LIMIT 1",
            (message,),
        ).fetchone()
        assert assistant_audit is not None
        assert assistant_audit["turn_policy"] == "full"
        assert assistant_audit["attachment_uuid"] is None

    monkeypatch.setattr(routes_memory_control, "get_settings", lambda: settings)
    with pytest.raises(HTTPException) as source_denied:
        routes_memory_control.fetch_attachment_sources(
            attachment,
            x_memorist_user_id="attacker",
            x_memorist_workspace_id=workspace,
        )
    assert source_denied.value.status_code == 404
    assert (
        routes_memory_control.fetch_attachment_sources(
            attachment,
            x_memorist_user_id=user,
            x_memorist_workspace_id=workspace,
        )["sources"]
        == []
    )
    with pytest.raises(HTTPException) as stale_regeneration:
        routes_memory_control.regenerate_without_recall(
            attachment,
            AttachmentActionRequest(idempotency_key=f"regeneration-{suffix}"),
            x_memorist_user_id=user,
            x_memorist_workspace_id=workspace,
        )
    assert stale_regeneration.value.status_code == 409
    with pytest.raises(HTTPException) as substituted:
        routes_memory_control.regenerate_without_recall(
            cancelled_attachment,
            AttachmentActionRequest(idempotency_key=f"regeneration-{suffix}"),
            x_memorist_user_id=user,
            x_memorist_workspace_id=workspace,
        )
    assert substituted.value.status_code == 409

    with _connection(settings) as restarted:
        persisted = MemoryControlRepository(
            restarted, settings.runtime_profile
        ).attachment_for_actor(attachment, user_uuid=user, workspace_uuid=workspace)
        assert persisted is not None
        assert persisted["lifecycle_status"] == "approved"
        cancelled_persisted = MemoryControlRepository(
            restarted, settings.runtime_profile
        ).attachment_for_actor(cancelled_attachment, user_uuid=user, workspace_uuid=workspace)
        assert cancelled_persisted is not None
        assert cancelled_persisted["lifecycle_status"] == "cancelled_before_send"
    if runtime == "full":
        assert not Path(settings.db_path).exists()
