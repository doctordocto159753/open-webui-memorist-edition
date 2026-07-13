from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from memcore.config import Settings
from memcore.main import create_app
from memcore.memory_control.policy import normalize_turn_policy
from memcore.memory_control.repository import MemoryControlRepository
from memcore.repositories import MemoryContextAttachmentRepository
from memcore.storage.migrations import apply_migrations
from memcore.storage.sqlite import connect


def _client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[TestClient, Path]:
    db_path = tmp_path / "memorist.sqlite"
    monkeypatch.setenv("MEMORIST_RUNTIME_PROFILE", "lite")
    monkeypatch.setenv("MEMORIST_CANONICAL_STORE", "sqlite")
    monkeypatch.setenv("MEMORIST_DB_PATH", str(db_path))
    monkeypatch.setenv("MEMORIST_OBJECT_STORE_PATH", str(tmp_path / "objects"))
    monkeypatch.setenv("MEMORIST_ENV", "test")
    monkeypatch.setenv("MEMORIST_ALLOW_LEGACY_ACTOR_HEADERS_FOR_TESTS", "true")
    return TestClient(create_app()), db_path


def _db(path: Path) -> sqlite3.Connection:
    connection = connect(str(path))
    apply_migrations(connection)
    return connection


def _counts(connection: sqlite3.Connection) -> dict[str, int]:
    tables = (
        "sessions",
        "messages",
        "jobs",
        "retrieval_runs",
        "memory_context_attachments",
        "model_usage_events",
        "memorist_turn_contracts",
        "memorist_policy_audit_events",
    )
    return {
        table: int(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
        for table in tables
    }


def test_normalized_policy_is_immutable_and_server_profile_is_absent() -> None:
    policy = normalize_turn_policy("private")
    assert policy.private is True
    assert "runtime_profile" not in policy.model_dump()
    with pytest.raises((ValidationError, AttributeError)):
        policy.recall_enabled = True


def test_policy_precedence_turn_chat_user_system(tmp_path: Path) -> None:
    settings = Settings(db_path=str(tmp_path / "policy.sqlite"))
    with connect(settings.db_path) as connection:
        apply_migrations(connection)
        repository = MemoryControlRepository(connection, "lite")
        repository.set_default(
            scope_type="system",
            scope_uuid="system",
            workspace_uuid="w",
            turn_policy="no_recall",
            attachment_review=False,
        )
        repository.set_default(
            scope_type="user",
            scope_uuid="u",
            workspace_uuid="w",
            turn_policy="full",
            attachment_review=False,
        )
        repository.set_default(
            scope_type="chat",
            scope_uuid="c",
            workspace_uuid="w",
            turn_policy="private",
            attachment_review=True,
        )
        chat = repository.resolve_policy(
            system_default="full",
            workspace_uuid="w",
            user_uuid="u",
            chat_uuid="c",
            turn_override=None,
        )
        assert chat.policy.mode.value == "private"
        assert chat.source == "chat"
        turn = repository.resolve_policy(
            system_default="full",
            workspace_uuid="w",
            user_uuid="u",
            chat_uuid="c",
            turn_override="no_recall",
        )
        assert turn.policy.mode.value == "no_recall"
        assert turn.source == "turn"


def test_private_turn_creates_no_memorist_data(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client, db_path = _client(monkeypatch, tmp_path)
    with _db(db_path) as connection:
        before = _counts(connection)
    response = client.post(
        "/memcore/openwebui/session/resolve",
        json={
            "openwebui_conversation_id": "private-chat",
            "user_id": "user-private",
            "turn_policy": "private",
        },
    )
    assert response.status_code == 200
    assert response.json() == {
        "private": True,
        "turn_policy": "private",
        "session_uuid": None,
    }
    capture = client.post(
        "/memcore/openwebui/messages/capture",
        json={
            "openwebui_conversation_id": "private-chat",
            "user_id": "user-private",
            "role": "user",
            "content": "must never persist",
            "turn_policy": "private",
        },
    )
    assert capture.status_code == 200
    assert capture.json()["skipped"] is True
    with _db(db_path) as connection:
        assert _counts(connection) == before


def test_no_recall_captures_both_sides_without_retrieval_or_attachment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client, db_path = _client(monkeypatch, tmp_path)
    session = client.post(
        "/memcore/openwebui/session/resolve",
        json={
            "openwebui_conversation_id": "no-recall-chat",
            "user_id": "user-a",
            "turn_policy": "no_recall",
        },
    ).json()
    captured = client.post(
        "/memcore/openwebui/messages/capture",
        json={
            "session_uuid": session["session_uuid"],
            "openwebui_conversation_id": "no-recall-chat",
            "user_id": "user-a",
            "workspace_uuid": session["workspace_uuid"],
            "role": "user",
            "content": "capture this but recall nothing",
            "idempotency_key": "no-recall-user-1",
            "turn_policy": "no_recall",
        },
    ).json()
    preflight = client.post(
        "/memcore/preflight",
        json={
            "session_uuid": session["session_uuid"],
            "input_message_uuid": captured["message_uuid"],
            "turn_policy": "full",
            "user_uuid": "user-a",
            "workspace_uuid": session["workspace_uuid"],
        },
    )
    assert preflight.status_code == 200
    assert preflight.json()["status"] == "disabled"
    assistant = client.post(
        "/memcore/assistant-response/completed",
        json={
            "input_message_uuid": captured["message_uuid"],
            "assistant_text": "captured assistant",
            "turn_policy": "no_recall",
            "user_uuid": "user-a",
            "workspace_uuid": session["workspace_uuid"],
        },
    )
    assert assistant.status_code == 200
    with _db(db_path) as connection:
        assert connection.execute("SELECT count(*) FROM messages").fetchone()[0] == 2
        assert connection.execute("SELECT count(*) FROM retrieval_runs").fetchone()[0] == 0
        assert (
            connection.execute("SELECT count(*) FROM memory_context_attachments").fetchone()[0] == 0
        )
        assert (
            connection.execute(
                "SELECT count(*) FROM jobs WHERE job_type = 'memory_extraction'"
            ).fetchone()[0]
            == 2
        )
        assert (
            connection.execute(
                "SELECT count(*) FROM memorist_policy_audit_events "
                "WHERE event_type = 'retrieval_suppressed'"
            ).fetchone()[0]
            == 1
        )


def test_attachment_authorization_lifecycle_and_regeneration_are_idempotent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client, db_path = _client(monkeypatch, tmp_path)
    session = client.post(
        "/memcore/openwebui/session/resolve",
        json={"openwebui_conversation_id": "lifecycle", "user_id": "owner"},
    ).json()
    captured = client.post(
        "/memcore/openwebui/messages/capture",
        json={
            "session_uuid": session["session_uuid"],
            "openwebui_conversation_id": "lifecycle",
            "user_id": "owner",
            "workspace_uuid": session["workspace_uuid"],
            "role": "user",
            "content": "original prompt",
            "idempotency_key": "lifecycle-user",
        },
    ).json()
    with _db(db_path) as connection:
        attachment = MemoryContextAttachmentRepository(connection).create_attachment(
            session_uuid=session["session_uuid"],
            input_message_uuid=captured["message_uuid"],
            attachment_mode="full",
            ijson_attachment={"provenance": []},
            rendered_attachment="bounded context",
        )
        substituted_attachment = MemoryContextAttachmentRepository(connection).create_attachment(
            session_uuid=session["session_uuid"],
            input_message_uuid=captured["message_uuid"],
            attachment_mode="full",
            ijson_attachment={"provenance": []},
            rendered_attachment="different bounded context",
        )
        connection.execute(
            """
            UPDATE memory_context_attachments
            SET owner_user_uuid = 'owner', workspace_uuid = ?, lifecycle_status = 'prepared'
            WHERE attachment_uuid = ?
            """,
            (session["workspace_uuid"], attachment.attachment_uuid),
        )
        connection.execute(
            """
            UPDATE memory_context_attachments
            SET owner_user_uuid = 'owner', workspace_uuid = ?, lifecycle_status = 'prepared'
            WHERE attachment_uuid = ?
            """,
            (session["workspace_uuid"], substituted_attachment.attachment_uuid),
        )
        connection.commit()
    wrong = client.get(
        f"/memcore/memory-control/attachments/{attachment.attachment_uuid}/preview",
        headers={
            "X-Memorist-User-Id": "attacker",
            "X-Memorist-Workspace-Id": session["workspace_uuid"],
        },
    )
    assert wrong.status_code == 404
    headers = {
        "X-Memorist-User-Id": "owner",
        "X-Memorist-Workspace-Id": session["workspace_uuid"],
    }
    payload = {"idempotency_key": "approve-idempotency"}
    first = client.post(
        f"/memcore/memory-control/attachments/{attachment.attachment_uuid}/approve",
        json=payload,
        headers=headers,
    )
    second = client.post(
        f"/memcore/memory-control/attachments/{attachment.attachment_uuid}/approve",
        json=payload,
        headers=headers,
    )
    assert first.status_code == second.status_code == 200
    resumed_capture = client.post(
        "/memcore/openwebui/messages/capture",
        json={
            "session_uuid": session["session_uuid"],
            "openwebui_conversation_id": "lifecycle",
            "user_id": "owner",
            "workspace_uuid": session["workspace_uuid"],
            "role": "user",
            "content": "original prompt",
            "idempotency_key": "lifecycle-user",
        },
    )
    assert resumed_capture.status_code == 200
    assert resumed_capture.json()["duplicate"] is True
    assert resumed_capture.json()["message_uuid"] == captured["message_uuid"]
    delivery = client.post(
        f"/memcore/memory-control/attachments/{attachment.attachment_uuid}/delivery",
        json={"idempotency_key": "delivery-idempotency"},
        headers=headers,
    )
    assert delivery.status_code == 200
    completion = client.post(
        "/memcore/assistant-response/completed",
        json={
            "input_message_uuid": captured["message_uuid"],
            "assistant_text": "response using delivered context",
            "attachment_uuid": attachment.attachment_uuid,
            "provider_response_id": "provider-lifecycle-response",
            "user_uuid": "owner",
            "workspace_uuid": session["workspace_uuid"],
        },
        headers=headers,
    )
    assert completion.status_code == 200
    rejection_payload = {"idempotency_key": "post-use-rejection"}
    rejected = client.post(
        f"/memcore/memory-control/attachments/{attachment.attachment_uuid}/rejection",
        json=rejection_payload,
        headers=headers,
    )
    repeated_rejection = client.post(
        f"/memcore/memory-control/attachments/{attachment.attachment_uuid}/rejection",
        json=rejection_payload,
        headers=headers,
    )
    assert rejected.status_code == repeated_rejection.status_code == 200
    preview = client.get(
        f"/memcore/memory-control/attachments/{attachment.attachment_uuid}/preview",
        headers=headers,
    ).json()
    assert preview["delivery_state"] == "used_for_response"
    assert preview["user_disposition"] == "rejected"
    regeneration = client.post(
        f"/memcore/memory-control/attachments/{attachment.attachment_uuid}/regenerate-without-recall",
        json={
            "idempotency_key": "regeneration-idempotency",
            "response_message_uuid": completion.json()["assistant_message_uuid"],
        },
        headers=headers,
    )
    assert regeneration.status_code == 200
    assert regeneration.json()["original_user_prompt"] == "original prompt"
    assert regeneration.json()["create_attachment"] is False
    regeneration_uuid = regeneration.json()["regeneration_uuid"]

    def complete(index: int) -> Any:
        return client.post(
            "/memcore/assistant-response/completed",
            json={
                "input_message_uuid": captured["message_uuid"],
                "assistant_text": f"regenerated variant {index}",
                "provider_response_id": f"regenerated-provider-{index}",
                "turn_policy": "no_recall",
                "user_uuid": "owner",
                "workspace_uuid": session["workspace_uuid"],
                "regeneration_uuid": regeneration_uuid,
            },
            headers=headers,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        completions = list(pool.map(complete, range(2)))
    assert all(response.status_code == 200 for response in completions)
    assert len({response.json()["assistant_message_uuid"] for response in completions}) == 1
    assert len({response.json()["response_link_uuid"] for response in completions}) == 1
    substituted = client.post(
        f"/memcore/memory-control/attachments/{substituted_attachment.attachment_uuid}"
        "/regenerate-without-recall",
        json={"idempotency_key": "regeneration-idempotency"},
        headers=headers,
    )
    assert substituted.status_code == 409
    with _db(db_path) as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM memorist_attachment_lifecycle_events "
                "WHERE attachment_uuid = ? AND lifecycle_status = 'approved'",
                (attachment.attachment_uuid,),
            ).fetchone()[0]
            == 1
        )
        assert (
            connection.execute("SELECT count(*) FROM memory_context_attachments").fetchone()[0] == 2
        )


def test_request_cannot_switch_runtime_profile(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client, _db_path = _client(monkeypatch, tmp_path)
    response = client.post(
        "/memcore/memory-control/policy/resolve",
        json={
            "user_uuid": "user",
            "memorist": {"turn_policy": "full", "runtime_profile": "lite"},
        },
    )
    assert response.status_code == 422


def test_retrieval_denies_missing_or_ownerless_turn_contract(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client, db_path = _client(monkeypatch, tmp_path)
    session = client.post(
        "/memcore/openwebui/session/resolve",
        json={"openwebui_conversation_id": "legacy-chat", "user_id": "owner"},
    ).json()
    captured = client.post(
        "/memcore/openwebui/messages/capture",
        json={
            "session_uuid": session["session_uuid"],
            "user_id": "owner",
            "workspace_uuid": session["workspace_uuid"],
            "role": "user",
            "content": "legacy prompt",
            "idempotency_key": "legacy-owner-capture",
        },
    ).json()
    with _db(db_path) as connection:
        connection.execute(
            "DELETE FROM memorist_turn_contracts WHERE input_message_uuid = ?",
            (captured["message_uuid"],),
        )
        connection.commit()
    denied = client.post(
        "/memcore/preflight",
        json={
            "session_uuid": session["session_uuid"],
            "input_message_uuid": captured["message_uuid"],
            "turn_policy": "full",
            "user_uuid": "owner",
            "workspace_uuid": session["workspace_uuid"],
        },
    )
    assert denied.status_code == 403


def test_expired_attachment_cannot_be_delivered(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client, db_path = _client(monkeypatch, tmp_path)
    session = client.post(
        "/memcore/openwebui/session/resolve",
        json={"openwebui_conversation_id": "expired", "user_id": "owner"},
    ).json()
    captured = client.post(
        "/memcore/openwebui/messages/capture",
        json={
            "session_uuid": session["session_uuid"],
            "user_id": "owner",
            "workspace_uuid": session["workspace_uuid"],
            "role": "user",
            "content": "prompt",
            "idempotency_key": "expired-user",
        },
    ).json()
    with _db(db_path) as connection:
        attachment = MemoryContextAttachmentRepository(connection).create_attachment(
            session_uuid=session["session_uuid"],
            input_message_uuid=captured["message_uuid"],
            attachment_mode="full",
            ijson_attachment={"provenance": []},
            rendered_attachment="stale context",
        )
        connection.execute(
            """
            UPDATE memory_context_attachments
            SET owner_user_uuid = 'owner', workspace_uuid = ?, expires_at = ?
            WHERE attachment_uuid = ?
            """,
            (session["workspace_uuid"], "2000-01-01T00:00:00+00:00", attachment.attachment_uuid),
        )
        connection.commit()
    response = client.post(
        f"/memcore/memory-control/attachments/{attachment.attachment_uuid}/delivery",
        json={"idempotency_key": "expired-delivery"},
        headers={
            "X-Memorist-User-Id": "owner",
            "X-Memorist-Workspace-Id": session["workspace_uuid"],
        },
    )
    assert response.status_code == 409
