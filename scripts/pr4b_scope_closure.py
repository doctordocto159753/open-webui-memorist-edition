from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(relative: str, old: str, new: str) -> None:
    path = ROOT / relative
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"expected exactly one match in {relative}, found {count}")
    path.write_text(text.replace(old, new), encoding="utf-8")


def write(relative: str, content: str) -> None:
    path = ROOT / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


replace_once(
    "open-webui-integration/memorist/backend/router.py",
    '''    client = MemoristClient()
    policy = client.resolve_turn_policy(
        user_id=actor.user_uuid,
        chat_id=conversation_id or temporary_chat_id,
        workspace_id=actor.workspace_uuid,
        request_control={"turn_policy": "full", "attachment_review": True},
    )
    if not policy.recall_enabled or not policy.attachment_enabled:
        return {"status": "disabled", "turn_policy": policy.mode}
''',
    '''    client = MemoristClient()
    requested_turn_policy = _optional_text(payload.get("turn_policy"))
    request_control: dict[str, Any] = {"attachment_review": True}
    if requested_turn_policy is not None:
        request_control["turn_policy"] = requested_turn_policy
    policy = client.resolve_turn_policy(
        user_id=actor.user_uuid,
        chat_id=conversation_id or temporary_chat_id,
        workspace_id=actor.workspace_uuid,
        request_control=request_control,
    )
    if bool(getattr(policy, "private", False)):
        return {"status": "private", "turn_policy": policy.mode}
    if not policy.recall_enabled or not policy.attachment_enabled:
        return {"status": "disabled", "turn_policy": policy.mode}
''',
)

replace_once(
    "open-webui-integration/memorist/ui/memoristClient.ts",
    '''  recent_conversation_text?: string | null;
};
''',
    '''  recent_conversation_text?: string | null;
  turn_policy?: MemoristTurnPolicy;
};
''',
)

replace_once(
    "memorist-core/src/memcore/api/routes_openwebui.py",
    '''from memcore.openwebui.commands import (
    CaptureIdempotencyConflict,
    CaptureOpenWebUIMessageCommand,
)
''',
    '''from memcore.openwebui.commands import (
    CaptureIdempotencyConflict,
    CaptureOpenWebUIMessageCommand,
)
from memcore.openwebui.model_scheduling import resolve_scoped_model_identity
''',
)

replace_once(
    "memorist-core/src/memcore/api/routes_openwebui.py",
    '''    if _is_full_postgres(settings):
        with _pg_connection(settings) as connection:
            row = connection.execute(
                "SELECT * FROM sessions WHERE session_uuid = %s",
                (session_uuid,),
            ).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="session not found")
            if actor is not None and row.get("workspace_uuid") != actor.workspace_uuid:
                raise HTTPException(status_code=404, detail="session not found")
            return {
                "session_uuid": session_uuid,
                "openwebui_conversation_id": row.get("openwebui_conversation_id"),
                "aliases": _pg_aliases(connection, session_uuid),
            }
    with _connection() as connection:
        session = SessionRepository(connection).get_session(session_uuid)
        if session is None:
            raise HTTPException(status_code=404, detail="session not found")
        if actor is not None and session.workspace_uuid != actor.workspace_uuid:
            raise HTTPException(status_code=404, detail="session not found")
        return {
            "session_uuid": session_uuid,
            "openwebui_conversation_id": session.openwebui_conversation_id,
            "aliases": list_aliases(connection, session_uuid),
        }
''',
    '''    if _is_full_postgres(settings):
        with _pg_connection(settings) as connection:
            row = connection.execute(
                "SELECT * FROM sessions WHERE session_uuid = %s",
                (session_uuid,),
            ).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="session not found")
            if actor is not None:
                _pg_assert_session_actor(
                    connection, session_uuid, actor.user_uuid, actor.workspace_uuid
                )
                if row.get("workspace_uuid") != actor.workspace_uuid:
                    raise HTTPException(status_code=404, detail="session not found")
            return {
                "session_uuid": session_uuid,
                "openwebui_conversation_id": row.get("openwebui_conversation_id"),
                "aliases": _pg_aliases(connection, session_uuid),
            }
    with _connection() as connection:
        session = SessionRepository(connection).get_session(session_uuid)
        if session is None:
            raise HTTPException(status_code=404, detail="session not found")
        if actor is not None:
            _assert_session_actor(connection, session_uuid, actor.user_uuid, actor.workspace_uuid)
            if session.workspace_uuid != actor.workspace_uuid:
                raise HTTPException(status_code=404, detail="session not found")
        return {
            "session_uuid": session_uuid,
            "openwebui_conversation_id": session.openwebui_conversation_id,
            "aliases": list_aliases(connection, session_uuid),
        }
''',
)

replace_once(
    "memorist-core/src/memcore/api/routes_openwebui.py",
    '''            session = _pg_legacy_session_by_conversation_id(
                connection, conversation_id, workspace_uuid
            )
''',
    '''            session = _pg_legacy_session_by_conversation_id(
                connection, conversation_id, user_id, workspace_uuid
            )
''',
)

replace_once(
    "memorist-core/src/memcore/api/routes_openwebui.py",
    '''def _pg_legacy_session_by_conversation_id(
    connection: Any,
    conversation_id: str | None,
    workspace_uuid: str,
) -> dict[str, Any] | None:
    if not conversation_id:
        return None
    row = connection.execute(
        """
        SELECT *
        FROM sessions
        WHERE openwebui_conversation_id = %s
          AND workspace_uuid = %s
        ORDER BY created_at
        LIMIT 1
        """,
        (conversation_id, workspace_uuid),
    ).fetchone()
    return dict(row) if row is not None else None
''',
    '''def _pg_legacy_session_by_conversation_id(
    connection: Any,
    conversation_id: str | None,
    user_id: str | None,
    workspace_uuid: str,
) -> dict[str, Any] | None:
    if not conversation_id or not user_id:
        return None
    row = connection.execute(
        """
        SELECT session.*
        FROM sessions AS session
        JOIN memorist_session_actors AS owner
          ON owner.session_uuid = session.session_uuid
        WHERE session.openwebui_conversation_id = %s
          AND session.workspace_uuid = %s
          AND owner.user_uuid = %s
          AND owner.workspace_uuid = %s
        ORDER BY session.created_at
        LIMIT 1
        """,
        (conversation_id, workspace_uuid, user_id, workspace_uuid),
    ).fetchone()
    return dict(row) if row is not None else None
''',
)

replace_once(
    "memorist-core/src/memcore/api/routes_openwebui.py",
    '''    profile = connection.execute(
        """
        SELECT p.model_profile_uuid, p.provider_type, p.model_name
        FROM model_role_defaults d
        JOIN model_profiles p ON p.model_profile_uuid = d.model_profile_uuid
        WHERE d.role = 'memory_extraction' AND COALESCE(p.is_enabled, true) = true
        ORDER BY CASE WHEN d.project_uuid IS NOT NULL THEN 0
                      WHEN d.workspace_uuid IS NOT NULL THEN 1 ELSE 2 END
        LIMIT 1
        """
    ).fetchone()
    model_identity = {
        "model_role": "memory_extraction",
        "model_profile_uuid": profile["model_profile_uuid"] if profile else None,
        "provider_type": profile["provider_type"] if profile else "deterministic",
        "model_name": profile["model_name"] if profile else "deterministic_extraction",
    }
''',
    '''    identity = resolve_scoped_model_identity(
        PostgresCompatConnection(connection), session_uuid
    )
    model_identity = identity.as_payload()
''',
)

replace_once(
    "memorist-core/src/memcore/api/routes_openwebui.py",
    '''                INSERT INTO model_usage_events (
                    usage_event_uuid, model_profile_uuid, role, event_type, stage,
                    provider_type, model_name, session_uuid, message_uuid, job_uuid,
                    input_tokens, output_tokens, embedding_count, status, created_at,
                    schema_version
                ) VALUES (%s, %s, 'memory_extraction', 'queued', 'memory_extraction_queued',
                          %s, %s, %s, %s, %s, 0, 0, 0, 'queued', %s, 1)
''',
    '''                INSERT INTO model_usage_events (
                    usage_event_uuid, model_profile_uuid, role, event_type, stage,
                    provider_type, model_name, workspace_uuid, project_uuid,
                    session_uuid, message_uuid, job_uuid, input_tokens, output_tokens,
                    embedding_count, status, created_at, schema_version
                ) VALUES (%s, %s, 'memory_extraction', 'queued', 'memory_extraction_queued',
                          %s, %s, %s, %s, %s, %s, %s, 0, 0, 0, 'queued', %s, 1)
''',
)

replace_once(
    "memorist-core/src/memcore/api/routes_openwebui.py",
    '''                    model_identity["provider_type"],
                    model_identity["model_name"],
                    session_uuid,
                    message_uuid,
                    job_uuid,
                    now,
''',
    '''                    model_identity["provider_type"],
                    model_identity["model_name"],
                    model_identity["workspace_uuid"],
                    model_identity["project_uuid"],
                    session_uuid,
                    message_uuid,
                    job_uuid,
                    now,
''',
)

replace_once(
    "memorist-core/src/memcore/openwebui/commands.py",
    '''from memcore.model_control.repository import ModelControlRepository
''',
    '''from memcore.model_control.repository import ModelControlRepository
from memcore.openwebui.model_scheduling import resolve_scoped_model_identity
''',
)

replace_once(
    "memorist-core/src/memcore/openwebui/commands.py",
    '''            model_control = ModelControlRepository(connection)
            extraction_default = model_control.resolve_default(ModelRole.MEMORY_EXTRACTION)
            extraction_profile_uuid = (
                str(extraction_default["model_profile_uuid"])
                if extraction_default is not None and extraction_default.get("model_profile_uuid")
                else None
            )
            job = JobRepository(connection).enqueue_job_once(
                "memory_extraction",
                {
                    "message_uuid": message.message_uuid,
                    "session_uuid": self.session_uuid,
                    "model_role": ModelRole.MEMORY_EXTRACTION.value,
                    "model_profile_uuid": extraction_profile_uuid,
                },
                priority=60,
            )
            model_control.record_usage_event(
                UsageEventCreate(
                    role=ModelRole.MEMORY_EXTRACTION,
                    stage="memory_extraction_queued",
                    model_profile_uuid=extraction_profile_uuid,
                    session_uuid=self.session_uuid,
                    message_uuid=message.message_uuid,
                    job_uuid=job.job_uuid,
                    status="queued",
                )
            )
''',
    '''            model_control = ModelControlRepository(connection)
            identity = resolve_scoped_model_identity(connection, self.session_uuid)
            job = JobRepository(connection).enqueue_job_once(
                "memory_extraction",
                {
                    "message_uuid": message.message_uuid,
                    "session_uuid": self.session_uuid,
                    **identity.as_payload(),
                },
                priority=60,
            )
            model_control.record_usage_event(
                UsageEventCreate(
                    role=ModelRole.MEMORY_EXTRACTION,
                    stage="memory_extraction_queued",
                    model_profile_uuid=identity.model_profile_uuid,
                    provider_type=identity.provider_type,
                    model_name=identity.model_name,
                    workspace_uuid=identity.workspace_uuid,
                    project_uuid=identity.project_uuid,
                    session_uuid=self.session_uuid,
                    message_uuid=message.message_uuid,
                    job_uuid=job.job_uuid,
                    status="queued",
                )
            )
''',
)

replace_once(
    "memorist-core/src/memcore/api/routes_retrieval.py",
    '''from memcore.models import ModelRole, PreflightResponse, PreflightStatus, new_uuid, utc_now
''',
    '''from memcore.models import ModelRole, PreflightResponse, PreflightStatus, new_uuid, utc_now
from memcore.openwebui.model_scheduling import resolve_scoped_model_identity
''',
)

replace_once(
    "memorist-core/src/memcore/api/routes_retrieval.py",
    '''        model_control = ModelControlRepository(connection)
        extraction_default = model_control.resolve_default(ModelRole.MEMORY_EXTRACTION)
        extraction_profile_uuid = (
            str(extraction_default["model_profile_uuid"])
            if extraction_default is not None and extraction_default.get("model_profile_uuid")
            else None
        )
        job = JobRepository(connection).enqueue_job_once(
            "memory_extraction",
            {
                "message_uuid": assistant_message.message_uuid,
                "session_uuid": input_message.session_uuid,
                "model_role": ModelRole.MEMORY_EXTRACTION.value,
                "model_profile_uuid": extraction_profile_uuid,
            },
            priority=60,
        )
        model_control.record_usage_event(
            UsageEventCreate(
                role=ModelRole.MEMORY_EXTRACTION,
                stage="memory_extraction_queued",
                model_profile_uuid=extraction_profile_uuid,
                session_uuid=input_message.session_uuid,
                message_uuid=assistant_message.message_uuid,
                attachment_uuid=request.attachment_uuid,
                job_uuid=job.job_uuid,
                status="queued",
            )
        )
''',
    '''        model_control = ModelControlRepository(connection)
        identity = resolve_scoped_model_identity(connection, input_message.session_uuid)
        job = JobRepository(connection).enqueue_job_once(
            "memory_extraction",
            {
                "message_uuid": assistant_message.message_uuid,
                "session_uuid": input_message.session_uuid,
                **identity.as_payload(),
            },
            priority=60,
        )
        model_control.record_usage_event(
            UsageEventCreate(
                role=ModelRole.MEMORY_EXTRACTION,
                stage="memory_extraction_queued",
                model_profile_uuid=identity.model_profile_uuid,
                provider_type=identity.provider_type,
                model_name=identity.model_name,
                workspace_uuid=identity.workspace_uuid,
                project_uuid=identity.project_uuid,
                session_uuid=input_message.session_uuid,
                message_uuid=assistant_message.message_uuid,
                attachment_uuid=request.attachment_uuid,
                job_uuid=job.job_uuid,
                status="queued",
            )
        )
''',
)

replace_once(
    "open-webui-integration/memorist/tests/test_backend_proxy.py",
    '''class FakeCoreClient:
    calls: list[dict[str, Any]] = []
''',
    '''class FakeCoreClient:
    calls: list[dict[str, Any]] = []
    policy_mode = "full"
''',
)

replace_once(
    "open-webui-integration/memorist/tests/test_backend_proxy.py",
    '''    def resolve_turn_policy(self, **kwargs: Any) -> SimpleNamespace:
        self.calls.append({"operation": "policy", **kwargs})
        return SimpleNamespace(mode="full", recall_enabled=True, attachment_enabled=True)
''',
    '''    def resolve_turn_policy(self, **kwargs: Any) -> SimpleNamespace:
        self.calls.append({"operation": "policy", **kwargs})
        mode = self.policy_mode
        return SimpleNamespace(
            mode=mode,
            private=mode == "private",
            recall_enabled=mode == "full",
            attachment_enabled=mode == "full",
        )
''',
)

replace_once(
    "open-webui-integration/memorist/tests/test_backend_proxy.py",
    '''def test_shipped_entrypoint_mounts_router_with_verified_openwebui_user(
''',
    '''def test_review_prepare_respects_effective_private_and_no_recall_policy() -> None:
    for mode, expected in (("private", "private"), ("no_recall", "disabled")):
        FakeCoreClient.calls = []
        FakeCoreClient.policy_mode = mode
        response = _app(authenticated=True).post(
            "/api/v1/memorist/memory-control/review/prepare",
            json={
                "conversation_id": f"chat-{mode}",
                "message_id": f"message-{mode}",
                "content": "must not force Full",
            },
        )
        assert response.status_code == 200
        assert response.json() == {"status": expected, "turn_policy": mode}
        policy_call = next(call for call in FakeCoreClient.calls if call.get("operation") == "policy")
        assert policy_call["request_control"] == {"attachment_review": True}
        assert not any(
            call.get("operation") in {"session", "capture", "preflight"}
            for call in FakeCoreClient.calls
        )
    FakeCoreClient.policy_mode = "full"


def test_review_prepare_forwards_only_explicit_turn_policy() -> None:
    FakeCoreClient.calls = []
    FakeCoreClient.policy_mode = "full"
    response = _app(authenticated=True).post(
        "/api/v1/memorist/memory-control/review/prepare",
        json={
            "conversation_id": "chat-explicit-full",
            "message_id": "message-explicit-full",
            "content": "explicit Full",
            "turn_policy": "full",
        },
    )
    assert response.status_code == 200
    policy_call = next(call for call in FakeCoreClient.calls if call.get("operation") == "policy")
    assert policy_call["request_control"] == {
        "attachment_review": True,
        "turn_policy": "full",
    }


def test_shipped_entrypoint_mounts_router_with_verified_openwebui_user(
''',
)

write(
    "memorist-core/src/memcore/openwebui/model_scheduling.py",
    '''from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from memcore.model_control.repository import built_in_default
from memcore.models import ModelRole


@dataclass(frozen=True)
class ScheduledModelIdentity:
    model_role: str
    model_profile_uuid: str | None
    provider_type: str
    model_name: str
    workspace_uuid: str | None
    project_uuid: str | None

    def as_payload(self) -> dict[str, str | None]:
        return {
            "model_role": self.model_role,
            "model_profile_uuid": self.model_profile_uuid,
            "provider_type": self.provider_type,
            "model_name": self.model_name,
            "workspace_uuid": self.workspace_uuid,
            "project_uuid": self.project_uuid,
        }


def resolve_scoped_model_identity(
    connection: Any,
    session_uuid: str,
    role: ModelRole = ModelRole.MEMORY_EXTRACTION,
) -> ScheduledModelIdentity:
    session = connection.execute(
        "SELECT workspace_uuid, project_uuid FROM sessions WHERE session_uuid = ?",
        (session_uuid,),
    ).fetchone()
    if session is None:
        raise LookupError(f"session not found for model scheduling: {session_uuid}")
    workspace_uuid = _text(session["workspace_uuid"])
    project_uuid = _text(session["project_uuid"])
    profile = connection.execute(
        """
        SELECT p.model_profile_uuid, p.provider_type, p.model_name
        FROM model_role_defaults AS d
        JOIN model_profiles AS p ON p.model_profile_uuid = d.model_profile_uuid
        WHERE d.role = ?
          AND p.role = d.role
          AND COALESCE(p.is_enabled, TRUE) = TRUE
          AND (
                (d.project_uuid IS NOT NULL AND d.project_uuid = ?)
             OR (
                d.project_uuid IS NULL
                AND d.workspace_uuid IS NOT NULL
                AND d.workspace_uuid = ?
             )
             OR (d.project_uuid IS NULL AND d.workspace_uuid IS NULL)
          )
          AND (
                COALESCE(p.requires_privacy_acknowledgement, FALSE) = FALSE
             OR p.privacy_acknowledged_at IS NOT NULL
          )
          AND (
                COALESCE(p.endpoint_is_local, TRUE) = TRUE
             OR p.privacy_acknowledged_at IS NOT NULL
          )
        ORDER BY CASE
            WHEN d.project_uuid IS NOT NULL AND d.project_uuid = ? THEN 0
            WHEN d.project_uuid IS NULL AND d.workspace_uuid = ? THEN 1
            ELSE 2
        END
        LIMIT 1
        """,
        (role.value, project_uuid, workspace_uuid, project_uuid, workspace_uuid),
    ).fetchone()
    if profile is None:
        fallback = built_in_default(role)
        return ScheduledModelIdentity(
            model_role=role.value,
            model_profile_uuid=None,
            provider_type=str(fallback["provider_type"]),
            model_name=str(fallback["model_name"]),
            workspace_uuid=workspace_uuid,
            project_uuid=project_uuid,
        )
    return ScheduledModelIdentity(
        model_role=role.value,
        model_profile_uuid=str(profile["model_profile_uuid"]),
        provider_type=str(profile["provider_type"]),
        model_name=str(profile["model_name"]),
        workspace_uuid=workspace_uuid,
        project_uuid=project_uuid,
    )


def _text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
''',
)

write(
    "memorist-core/tests/test_pr4b_scope_closure.py",
    '''from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from memcore.config import Settings, get_settings
from memcore.imports.runtime import initialize_runtime_storage
from memcore.main import create_app
from memcore.memory_control import memory_control_connection
from memcore.model_control.repository import ModelControlRepository
from memcore.model_control.schemas import ModelProfileCreate, ProviderType
from memcore.models import ModelRole, utc_now


RUNTIME = os.getenv("MEMORIST_SCOPE_TEST_RUNTIME", "lite")


@pytest.fixture
def runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, Settings]:
    monkeypatch.setenv("MEMORIST_ENV", "test")
    monkeypatch.setenv("MEMORIST_ALLOW_LEGACY_ACTOR_HEADERS_FOR_TESTS", "true")
    monkeypatch.setenv("MEMORIST_RUNTIME_PROFILE", RUNTIME)
    monkeypatch.setenv("MEMORIST_OBJECT_STORE_PATH", str(tmp_path / "objects"))
    if RUNTIME == "full":
        dsn = os.getenv("MEMORIST_POSTGRES_DSN")
        if not dsn:
            pytest.fail("Full scope-closure certification requires PostgreSQL")
        monkeypatch.setenv("MEMORIST_CANONICAL_STORE", "postgres")
        monkeypatch.setenv("MEMORIST_POSTGRES_DSN", dsn)
        monkeypatch.setenv("MEMORIST_GRAPH_BACKEND", "disabled")
        monkeypatch.setenv("MEMORIST_ALLOW_FULL_GRAPH_DEGRADED", "true")
        monkeypatch.setenv("MEMORIST_HOT_SCHEDULER", "in_memory")
    else:
        monkeypatch.setenv("MEMORIST_CANONICAL_STORE", "sqlite")
        monkeypatch.setenv("MEMORIST_DB_PATH", str(tmp_path / "scope.sqlite3"))
        monkeypatch.setenv("MEMORIST_GRAPH_BACKEND", "disabled")
        monkeypatch.setenv("MEMORIST_HOT_SCHEDULER", "disabled")
    get_settings.cache_clear()
    settings = get_settings()
    initialize_runtime_storage(settings)
    client = TestClient(create_app())
    return client, settings


def _headers(user: str, workspace: str) -> dict[str, str]:
    return {
        "X-Memorist-User-Id": user,
        "X-Memorist-Workspace-Id": workspace,
    }


def _seed_session(
    settings: Settings,
    *,
    user: str | None,
    conversation_id: str,
    workspace: str | None = None,
    project: str | None = None,
) -> dict[str, str]:
    suffix = uuid4().hex
    workspace = workspace or f"workspace-{suffix}"
    project = project or f"project-{suffix}"
    session = f"session-{suffix}"
    now = utc_now()
    with memory_control_connection(settings) as connection:
        connection.execute(
            "INSERT INTO workspaces (workspace_uuid, name, created_at, updated_at, schema_version) "
            "VALUES (?, ?, ?, ?, 1)",
            (workspace, workspace, now, now),
        )
        connection.execute(
            "INSERT INTO projects (project_uuid, workspace_uuid, name, created_at, updated_at, schema_version) "
            "VALUES (?, ?, ?, ?, ?, 1)",
            (project, workspace, project, now, now),
        )
        connection.execute(
            "INSERT INTO sessions (session_uuid, workspace_uuid, project_uuid, "
            "openwebui_conversation_id, status, created_at, updated_at, schema_version) "
            "VALUES (?, ?, ?, ?, 'active', ?, ?, 1)",
            (session, workspace, project, conversation_id, now, now),
        )
        if user is not None:
            connection.execute(
                "INSERT INTO memorist_session_actors "
                "(session_uuid, user_uuid, workspace_uuid, created_at, schema_version) "
                "VALUES (?, ?, ?, ?, 1)",
                (session, user, workspace, now),
            )
        connection.commit()
    return {
        "workspace": workspace,
        "project": project,
        "session": session,
        "conversation": conversation_id,
        "user": user or "",
    }


def test_ownerless_legacy_session_cannot_be_claimed(runtime: tuple[TestClient, Settings]) -> None:
    client, settings = runtime
    legacy = _seed_session(
        settings,
        user=None,
        conversation_id=f"legacy-{uuid4().hex}",
    )
    attacker = f"attacker-{uuid4().hex}"
    response = client.post(
        "/memcore/openwebui/session/resolve",
        json={
            "openwebui_conversation_id": legacy["conversation"],
            "user_id": attacker,
            "workspace_uuid": legacy["workspace"],
            "turn_policy": "no_recall",
        },
        headers=_headers(attacker, legacy["workspace"]),
    )
    assert response.status_code == 200, response.text
    resolved = response.json()
    assert resolved["session_uuid"] != legacy["session"]
    with memory_control_connection(settings) as connection:
        assert connection.execute(
            "SELECT 1 FROM memorist_session_actors WHERE session_uuid = ?",
            (legacy["session"],),
        ).fetchone() is None
        owner = connection.execute(
            "SELECT user_uuid, workspace_uuid FROM memorist_session_actors "
            "WHERE session_uuid = ?",
            (resolved["session_uuid"],),
        ).fetchone()
    assert owner is not None
    assert owner["user_uuid"] == attacker
    assert owner["workspace_uuid"] == legacy["workspace"]


def test_session_lineage_requires_exact_owner(runtime: tuple[TestClient, Settings]) -> None:
    client, settings = runtime
    owner = f"owner-{uuid4().hex}"
    seeded = _seed_session(
        settings,
        user=owner,
        conversation_id=f"owned-{uuid4().hex}",
    )
    owner_response = client.get(
        f"/memcore/openwebui/sessions/{seeded['session']}/lineage",
        headers=_headers(owner, seeded["workspace"]),
    )
    assert owner_response.status_code == 200, owner_response.text
    attacker_response = client.get(
        f"/memcore/openwebui/sessions/{seeded['session']}/lineage",
        headers=_headers(f"attacker-{uuid4().hex}", seeded["workspace"]),
    )
    assert attacker_response.status_code == 404
    ownerless = _seed_session(
        settings,
        user=None,
        conversation_id=f"ownerless-{uuid4().hex}",
    )
    ownerless_response = client.get(
        f"/memcore/openwebui/sessions/{ownerless['session']}/lineage",
        headers=_headers(owner, ownerless["workspace"]),
    )
    assert ownerless_response.status_code == 404


def _profile(connection: object, name: str) -> str:
    profile = ModelControlRepository(connection).create_profile(
        ModelProfileCreate(
            provider_type=ProviderType.DETERMINISTIC,
            model_name=name,
            role=ModelRole.MEMORY_EXTRACTION,
            endpoint_is_local=True,
        )
    )
    return profile.model_profile_uuid


def _capture(
    client: TestClient,
    *,
    seeded: dict[str, str],
    content: str,
    key: str,
) -> dict[str, object]:
    response = client.post(
        "/memcore/openwebui/messages/capture",
        json={
            "session_uuid": seeded["session"],
            "openwebui_conversation_id": seeded["conversation"],
            "user_id": seeded["user"],
            "workspace_uuid": seeded["workspace"],
            "role": "user",
            "content": content,
            "idempotency_key": key,
            "turn_policy": "no_recall",
        },
        headers=_headers(seeded["user"], seeded["workspace"]),
    )
    assert response.status_code == 200, response.text
    return response.json()


def _job_payload(settings: Settings, message_uuid: str) -> dict[str, object]:
    with memory_control_connection(settings) as connection:
        row = connection.execute(
            "SELECT payload_ijson FROM jobs WHERE job_type = 'memory_extraction' "
            "AND payload_ijson LIKE ? ORDER BY created_at DESC LIMIT 1",
            (f"%{message_uuid}%",),
        ).fetchone()
    assert row is not None
    return json.loads(str(row["payload_ijson"]))


def test_processing_node_resolution_is_project_workspace_global_scoped(
    runtime: tuple[TestClient, Settings],
) -> None:
    client, settings = runtime
    workspace_a = f"workspace-a-{uuid4().hex}"
    project_a = f"project-a-{uuid4().hex}"
    project_a_fallback = f"project-a-fallback-{uuid4().hex}"
    workspace_b = f"workspace-b-{uuid4().hex}"
    project_b = f"project-b-{uuid4().hex}"
    user_a = f"user-a-{uuid4().hex}"
    user_b = f"user-b-{uuid4().hex}"
    session_project_a = _seed_session(
        settings,
        user=user_a,
        conversation_id=f"chat-project-a-{uuid4().hex}",
        workspace=workspace_a,
        project=project_a,
    )
    session_workspace_a = _seed_session(
        settings,
        user=user_a,
        conversation_id=f"chat-workspace-a-{uuid4().hex}",
        workspace=workspace_a,
        project=project_a_fallback,
    )
    session_workspace_b = _seed_session(
        settings,
        user=user_b,
        conversation_id=f"chat-workspace-b-{uuid4().hex}",
        workspace=workspace_b,
        project=project_b,
    )
    with memory_control_connection(settings) as connection:
        model_control = ModelControlRepository(connection)
        global_profile = _profile(connection, "global-profile")
        workspace_a_profile = _profile(connection, "workspace-a-profile")
        project_a_profile = _profile(connection, "project-a-profile")
        foreign_project_profile = _profile(connection, "foreign-project-profile")
        model_control.set_default(ModelRole.MEMORY_EXTRACTION, global_profile)
        model_control.set_default(
            ModelRole.MEMORY_EXTRACTION,
            workspace_a_profile,
            workspace_uuid=workspace_a,
        )
        model_control.set_default(
            ModelRole.MEMORY_EXTRACTION,
            project_a_profile,
            workspace_uuid=workspace_a,
            project_uuid=project_a,
        )
        model_control.set_default(
            ModelRole.MEMORY_EXTRACTION,
            foreign_project_profile,
            workspace_uuid=workspace_b,
            project_uuid=project_b,
        )
    project_capture = _capture(
        client,
        seeded=session_project_a,
        content="project scoped content",
        key=f"capture-{uuid4().hex}",
    )
    workspace_capture = _capture(
        client,
        seeded=session_workspace_a,
        content="workspace scoped content",
        key=f"capture-{uuid4().hex}",
    )
    foreign_capture = _capture(
        client,
        seeded=session_workspace_b,
        content="foreign project content",
        key=f"capture-{uuid4().hex}",
    )
    project_payload = _job_payload(settings, str(project_capture["message_uuid"]))
    workspace_payload = _job_payload(settings, str(workspace_capture["message_uuid"]))
    foreign_payload = _job_payload(settings, str(foreign_capture["message_uuid"]))
    assert project_payload["model_profile_uuid"] == project_a_profile
    assert project_payload["workspace_uuid"] == workspace_a
    assert project_payload["project_uuid"] == project_a
    assert workspace_payload["model_profile_uuid"] == workspace_a_profile
    assert workspace_payload["workspace_uuid"] == workspace_a
    assert workspace_payload["project_uuid"] == project_a_fallback
    assert foreign_payload["model_profile_uuid"] == foreign_project_profile
    assert project_payload["model_profile_uuid"] != foreign_project_profile
    with memory_control_connection(settings) as connection:
        usage = connection.execute(
            "SELECT model_profile_uuid, provider_type, model_name, workspace_uuid, project_uuid "
            "FROM model_usage_events WHERE message_uuid = ? AND stage = 'memory_extraction_queued'",
            (project_capture["message_uuid"],),
        ).fetchone()
    assert usage is not None
    assert usage["model_profile_uuid"] == project_a_profile
    assert usage["workspace_uuid"] == workspace_a
    assert usage["project_uuid"] == project_a
    duplicate = _capture(
        client,
        seeded=session_project_a,
        content="project scoped content",
        key=str(
            next(
                json.loads(str(row["payload_ijson"])).get("unused", "")
                for row in []
            )
        ) if False else "duplicate-key-not-used",
    )
    assert duplicate["duplicate"] is False
''',
)

# Replace the intentionally simple final duplicate block with a real replay assertion.
test_path = ROOT / "memorist-core/tests/test_pr4b_scope_closure.py"
test_text = test_path.read_text(encoding="utf-8")
test_text = test_text.replace(
    '''    project_capture = _capture(
        client,
        seeded=session_project_a,
        content="project scoped content",
        key=f"capture-{uuid4().hex}",
    )
''',
    '''    project_key = f"capture-{uuid4().hex}"
    project_capture = _capture(
        client,
        seeded=session_project_a,
        content="project scoped content",
        key=project_key,
    )
''',
)
test_text = test_text.replace(
    '''    duplicate = _capture(
        client,
        seeded=session_project_a,
        content="project scoped content",
        key=str(
            next(
                json.loads(str(row["payload_ijson"])).get("unused", "")
                for row in []
            )
        ) if False else "duplicate-key-not-used",
    )
    assert duplicate["duplicate"] is False
''',
    '''    duplicate = _capture(
        client,
        seeded=session_project_a,
        content="project scoped content",
        key=project_key,
    )
    assert duplicate["duplicate"] is True
    with memory_control_connection(settings) as connection:
        job_count = connection.execute(
            "SELECT count(*) FROM jobs WHERE job_type = 'memory_extraction' "
            "AND payload_ijson LIKE ?",
            (f"%{project_capture['message_uuid']}%",),
        ).fetchone()[0]
        usage_count = connection.execute(
            "SELECT count(*) FROM model_usage_events WHERE message_uuid = ? "
            "AND stage = 'memory_extraction_queued'",
            (project_capture["message_uuid"],),
        ).fetchone()[0]
    assert job_count == 1
    assert usage_count == 1
''',
)
test_path.write_text(test_text, encoding="utf-8")

replace_once(
    ".github/workflows/pr4b-memory-control.yml",
    '''  type-and-lint:
''',
    '''  scope-closure-lite:
    name: Scope Isolation and Policy Safety — Lite
    runs-on: ubuntu-latest
    env:
      MEMORIST_SCOPE_TEST_RUNTIME: lite
      MEMORIST_RUNTIME_PROFILE: lite
      MEMORIST_CANONICAL_STORE: sqlite
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - working-directory: memorist-core
        run: uv sync --all-extras
      - working-directory: memorist-core
        run: |
          uv run pytest tests/test_pr4b_scope_closure.py ../open-webui-integration/memorist/tests/test_backend_proxy.py -q --junitxml=scope-closure-lite.xml
          uv run python -c "import xml.etree.ElementTree as E; r=E.parse('scope-closure-lite.xml').getroot(); assert list(r.iter('testcase')) and not list(r.iter('skipped'))"

  scope-closure-full:
    name: Scope Isolation and Policy Safety — Full PostgreSQL
    runs-on: ubuntu-latest
    services: *postgres
    env:
      <<: *full_env
      MEMORIST_SCOPE_TEST_RUNTIME: full
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - working-directory: memorist-core
        run: uv sync --all-extras
      - working-directory: memorist-core
        run: |
          uv run pytest tests/test_pr4b_scope_closure.py -q --junitxml=scope-closure-full.xml
          uv run python -c "import xml.etree.ElementTree as E; r=E.parse('scope-closure-full.xml').getroot(); assert list(r.iter('testcase')) and not list(r.iter('skipped'))"

  type-and-lint:
''',
)

print("PR4-B scope closure patch applied")
