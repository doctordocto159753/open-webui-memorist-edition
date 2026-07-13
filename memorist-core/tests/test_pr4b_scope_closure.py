from __future__ import annotations

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
from memcore.model_control.postgres_repository import PostgresModelControlRepository
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
    workspace = workspace or str(uuid4())
    project = project or str(uuid4())
    session = str(uuid4())
    now = utc_now()
    with memory_control_connection(settings) as connection:
        connection.execute(
            "INSERT INTO workspaces (workspace_uuid, name, created_at, updated_at, schema_version) "
            "VALUES (?, ?, ?, ?, 1) ON CONFLICT (workspace_uuid) DO NOTHING",
            (workspace, workspace, now, now),
        )
        connection.execute(
            "INSERT INTO projects "
            "(project_uuid, workspace_uuid, name, created_at, updated_at, schema_version) "
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
        assert (
            connection.execute(
                "SELECT 1 FROM memorist_session_actors WHERE session_uuid = ?",
                (legacy["session"],),
            ).fetchone()
            is None
        )
        owner = connection.execute(
            "SELECT user_uuid, workspace_uuid FROM memorist_session_actors WHERE session_uuid = ?",
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


def _model_control(connection: object) -> ModelControlRepository:
    if RUNTIME == "full":
        return PostgresModelControlRepository(connection)
    return ModelControlRepository(connection)


def _profile(connection: object, name: str) -> str:
    profile = _model_control(connection).create_profile(
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
    workspace_a = str(uuid4())
    project_a = str(uuid4())
    project_a_fallback = str(uuid4())
    workspace_b = str(uuid4())
    project_b = str(uuid4())
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
    workspace_c = str(uuid4())
    project_c = str(uuid4())
    user_c = f"user-c-{uuid4().hex}"
    session_global = _seed_session(
        settings,
        user=user_c,
        conversation_id=f"chat-global-{uuid4().hex}",
        workspace=workspace_c,
        project=project_c,
    )
    with memory_control_connection(settings) as connection:
        model_control = _model_control(connection)
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
        connection.commit()
    project_key = f"capture-{uuid4().hex}"
    project_capture = _capture(
        client,
        seeded=session_project_a,
        content="project scoped content",
        key=project_key,
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
    global_capture = _capture(
        client,
        seeded=session_global,
        content="global fallback content",
        key=f"capture-{uuid4().hex}",
    )
    project_payload = _job_payload(settings, str(project_capture["message_uuid"]))
    workspace_payload = _job_payload(settings, str(workspace_capture["message_uuid"]))
    foreign_payload = _job_payload(settings, str(foreign_capture["message_uuid"]))
    global_payload = _job_payload(settings, str(global_capture["message_uuid"]))
    assert project_payload["model_profile_uuid"] == project_a_profile
    assert project_payload["workspace_uuid"] == workspace_a
    assert project_payload["project_uuid"] == project_a
    assert workspace_payload["model_profile_uuid"] == workspace_a_profile
    assert workspace_payload["workspace_uuid"] == workspace_a
    assert workspace_payload["project_uuid"] == project_a_fallback
    assert foreign_payload["model_profile_uuid"] == foreign_project_profile
    assert global_payload["model_profile_uuid"] == global_profile
    assert global_payload["workspace_uuid"] == workspace_c
    assert global_payload["project_uuid"] == project_c
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

    assistant = client.post(
        "/memcore/assistant-response/completed",
        json={
            "input_message_uuid": project_capture["message_uuid"],
            "assistant_text": "assistant scoped content",
            "provider_response_id": f"provider-{uuid4().hex}",
            "turn_policy": "no_recall",
            "user_uuid": user_a,
            "workspace_uuid": workspace_a,
        },
        headers=_headers(user_a, workspace_a),
    )
    assert assistant.status_code == 200, assistant.text
    assistant_payload = _job_payload(settings, str(assistant.json()["assistant_message_uuid"]))
    assert assistant_payload["model_profile_uuid"] == project_a_profile
    assert assistant_payload["workspace_uuid"] == workspace_a
    assert assistant_payload["project_uuid"] == project_a
