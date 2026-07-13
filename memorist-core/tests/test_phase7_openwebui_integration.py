from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from memcore.main import create_app

ROOT = Path(__file__).resolve().parents[2]
INTEGRATION_ROOT = ROOT / "open-webui-integration" / "memorist"
if str(INTEGRATION_ROOT) not in sys.path:
    sys.path.insert(0, str(INTEGRATION_ROOT))


def test_openwebui_capture_endpoints_and_status(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("MEMORIST_DB_PATH", str(tmp_path / "memorist.sqlite"))
    monkeypatch.setenv("MEMORIST_OBJECT_STORE_PATH", str(tmp_path / "objects"))
    app = create_app()
    client = TestClient(app)

    session_response = client.post(
        "/memcore/openwebui/session/resolve",
        json={
            "openwebui_conversation_id": "chat-1",
            "title": "Chat",
            "user_id": "phase7-user",
        },
    )
    assert session_response.status_code == 200
    session_uuid = session_response.json()["session_uuid"]

    capture_response = client.post(
        "/memcore/openwebui/messages/capture",
        json={
            "session_uuid": session_uuid,
            "openwebui_conversation_id": "chat-1",
            "openwebui_message_id": "u1",
            "role": "user",
            "content": "hello",
            "idempotency_key": "capture-1",
        },
    )
    assert capture_response.status_code == 200
    assert capture_response.json()["duplicate"] is False

    duplicate_response = client.post(
        "/memcore/openwebui/messages/capture",
        json={
            "session_uuid": session_uuid,
            "role": "user",
            "content": "hello",
            "idempotency_key": "capture-1",
        },
    )
    assert duplicate_response.json()["duplicate"] is True

    missing_session_response = client.post(
        "/memcore/openwebui/messages/capture",
        json={
            "session_uuid": "00000000-0000-0000-0000-000000000000",
            "role": "user",
            "content": "hello",
        },
    )
    assert missing_session_response.status_code == 404

    status = client.get("/memcore/openwebui/status")
    assert status.status_code == 200
    assert status.json()["memorist_core"] == "connected"


def test_integration_filter_imports() -> None:
    import importlib

    filter_module: Any = importlib.import_module("filter.memorist_memory_filter")
    function_module: Any = importlib.import_module("function.memorist_status_function")

    assert filter_module.Filter is not None
    assert function_module.Tools is not None
