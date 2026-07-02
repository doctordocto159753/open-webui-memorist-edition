from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from memcore.imports.repositories import ImportRepository
from memcore.main import create_app
from memcore.storage.migrations import apply_migrations
from memcore.storage.sqlite import connect
from memcore.storage.write_actor import get_write_actor


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> TestClient:
    monkeypatch.setenv("MEMORIST_DB_PATH", str(tmp_path / "memorist.sqlite"))
    monkeypatch.setenv("MEMORIST_OBJECT_STORE_PATH", str(tmp_path / "objects"))
    monkeypatch.setenv("MEMORIST_UNKNOWN_MODEL_CONTEXT_WINDOW", "4096")
    monkeypatch.setenv("MEMORIST_IMPORT_MAX_WRITE_QUEUE_DEPTH", "2")
    return TestClient(create_app())


def test_base_apis_create_and_trace_lineage(client: TestClient) -> None:
    workspace = _assert_ok(
        client.post("/memcore/workspaces", json={"name": "Workspace"})
    )
    project = _assert_ok(
        client.post(
            "/memcore/projects",
            json={"workspace_uuid": workspace["workspace_uuid"], "name": "Project"},
        )
    )
    session = _assert_ok(
        client.post(
            "/memcore/sessions",
            json={
                "workspace_uuid": workspace["workspace_uuid"],
                "project_uuid": project["project_uuid"],
                "title": "Session",
            },
        )
    )
    message = _assert_ok(
        client.post(
            "/memcore/messages",
            json={
                "session_uuid": session["session_uuid"],
                "role": "user",
                "creator_type": "user",
                "raw_text": "Daily-use base API message",
            },
        )
    )

    messages = _assert_ok(client.get(f"/memcore/sessions/{session['session_uuid']}/messages"))
    lineage = _assert_ok(client.get(f"/memcore/messages/{message['message_uuid']}/lineage"))

    assert messages["items"][0]["message_uuid"] == message["message_uuid"]
    assert lineage["message"]["message_uuid"] == message["message_uuid"]
    assert set(lineage) >= {
        "session",
        "message",
        "message_versions",
        "events",
        "text_units",
        "attachments",
        "delivery_events",
    }


def test_openwebui_capture_uses_writer_idempotency(client: TestClient) -> None:
    session = _assert_ok(
        client.post(
            "/memcore/openwebui/session/resolve",
            json={"openwebui_conversation_id": "daily-idempotent", "user_id": "user-1"},
        )
    )
    payload = {
        "session_uuid": session["session_uuid"],
        "openwebui_conversation_id": "daily-idempotent",
        "openwebui_message_id": "message-1",
        "role": "user",
        "content": "hello",
        "idempotency_key": "capture-idempotent-1",
    }

    first = _assert_ok(client.post("/memcore/openwebui/messages/capture", json=payload))
    second = _assert_ok(client.post("/memcore/openwebui/messages/capture", json=payload))
    diagnostics = _assert_ok(client.get("/memcore/diagnostics/write-actor"))

    assert first["duplicate"] is False
    assert second["duplicate"] is True
    assert second["message_uuid"] == first["message_uuid"]
    assert diagnostics["submitted_count"] >= 3
    assert diagnostics["last_command_type"] in {
        "openwebui_capture_message",
        "openwebui_resolve_session",
    }


def test_model_registry_drives_unknown_model_budget(client: TestClient) -> None:
    fallback = _assert_ok(
        client.post(
            "/memcore/budget/attachment",
            json={"target_model": "daily-model", "recent_conversation_text": "short"},
        )
    )
    registry_entry = _assert_ok(
        client.post(
            "/memcore/model-registry",
            json={
                "provider": "local",
                "model_name": "daily-model",
                "model_aliases": ["daily-model", "daily-alias"],
                "context_window": 32768,
                "source": "test",
            },
        )
    )
    patched = _assert_ok(
        client.patch(
            f"/memcore/model-registry/{registry_entry['model_profile_uuid']}",
            json={"context_window": 65536, "confidence": 0.9},
        )
    )
    resolved = _assert_ok(
        client.post(
            "/memcore/budget/attachment",
            json={
                "model_provider": "local",
                "target_model": "daily-alias",
                "recent_conversation_text": "short",
            },
        )
    )

    assert fallback["effective_token_budget"] < resolved["effective_token_budget"]
    assert patched["context_window"] == 65536
    assert "unknown model" not in " ".join(resolved["warnings"]).lower()


def test_import_progress_pause_resume_cancel(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "memorist.sqlite"
    connection = connect(db_path)
    try:
        apply_migrations(connection)
        run = ImportRepository(connection).create_run("a" * 64, "inspect", {})
    finally:
        connection.close()

    paused = _assert_ok(client.post(f"/memcore/imports/{run['import_run_uuid']}/pause"))
    progress = _assert_ok(client.get(f"/memcore/imports/{run['import_run_uuid']}/progress"))
    resumed = _assert_ok(client.post(f"/memcore/imports/{run['import_run_uuid']}/resume"))
    cancelled = _assert_ok(client.post(f"/memcore/imports/{run['import_run_uuid']}/cancel"))

    assert paused["paused"] is True
    assert progress["paused"] is True
    assert resumed["paused"] is False
    assert cancelled["cancelled"] is True


def test_daily_diagnostics_report_backpressure(client: TestClient, tmp_path: Path) -> None:
    actor = get_write_actor(tmp_path / "memorist.sqlite")
    queue: Any = actor._queue
    for _index in range(3):
        queue.put(None)

    diagnostics = _assert_ok(client.get("/memcore/diagnostics/daily"))

    assert diagnostics["queues"]["write_depth"] >= 3
    assert "write_queue_depth_high" in diagnostics["warnings"]

    while not queue.empty():
        queue.get_nowait()


def _assert_ok(response: Any) -> dict[str, Any]:
    assert response.status_code == 200, response.text
    body = response.json()
    assert isinstance(body, dict)
    return body
