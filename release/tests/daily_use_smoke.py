from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
CORE_SRC = ROOT / "memorist-core" / "src"
if str(CORE_SRC) not in sys.path:
    sys.path.insert(0, str(CORE_SRC))

from memcore.config import get_settings  # noqa: E402
from memcore.main import create_app  # noqa: E402
from memcore.storage.write_actor import get_write_actor  # noqa: E402


def run() -> dict[str, Any]:
    exit_code = main()
    return {
        "name": "daily_use_smoke",
        "passed": exit_code == 0,
        "classification": "real",
        "failing_step": None if exit_code == 0 else "daily_use_smoke",
        "remediation_hint": None if exit_code == 0 else "Run `make smoke-daily` locally.",
    }


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="memorist-daily-smoke-") as temp_dir:
        base = Path(temp_dir)
        os.environ["MEMORIST_ENV"] = "development"
        os.environ["MEMORIST_LOCAL_ONLY"] = "true"
        os.environ["MEMORIST_DB_PATH"] = str(base / "memorist.sqlite")
        os.environ["MEMORIST_OBJECT_STORE_PATH"] = str(base / "objects")
        os.environ["MEMORIST_RETRIEVAL_MODE"] = "standard"
        get_settings.cache_clear()

        db_path = os.environ["MEMORIST_DB_PATH"]
        with TestClient(create_app()) as client:
            checks = [
                _check_health(client),
                _check_base_flow(client),
                _check_openwebui_capture(client),
                _check_budget(client),
                _check_diagnostics(client),
            ]
        get_write_actor(db_path).stop_sync()
        get_settings.cache_clear()
        failures = [check for check in checks if check["status"] != "ok"]
        if failures:
            print(json.dumps({"status": "failed", "failures": failures}))
            return 1
        print(json.dumps({"status": "ok", "checks": checks}))
        return 0


def _check_health(client: TestClient) -> dict[str, Any]:
    response = client.get("/memcore/health")
    return {"name": "health", "status": "ok" if response.status_code == 200 else "failed"}


def _check_base_flow(client: TestClient) -> dict[str, Any]:
    workspace = client.post("/memcore/workspaces", json={"name": "Daily Smoke"}).json()
    session = client.post(
        "/memcore/sessions",
        json={"workspace_uuid": workspace["workspace_uuid"], "title": "Daily Session"},
    ).json()
    message = client.post(
        "/memcore/messages",
        json={
            "session_uuid": session["session_uuid"],
            "role": "user",
            "creator_type": "user",
            "raw_text": "remember this local preference",
        },
    ).json()
    lineage = client.get(f"/memcore/messages/{message['message_uuid']}/lineage")
    status = "ok" if lineage.status_code == 200 and lineage.json()["message"] else "failed"
    return {"name": "base_flow", "status": status}


def _check_openwebui_capture(client: TestClient) -> dict[str, Any]:
    session = client.post(
        "/memcore/openwebui/session/resolve",
        json={"openwebui_conversation_id": "daily-chat", "user_id": "local-user"},
    ).json()
    first = client.post(
        "/memcore/openwebui/messages/capture",
        json={
            "session_uuid": session["session_uuid"],
            "openwebui_conversation_id": "daily-chat",
            "openwebui_message_id": "daily-msg-1",
            "role": "user",
            "content": "hello from Open WebUI",
            "idempotency_key": "daily-capture-1",
        },
    )
    second = client.post(
        "/memcore/openwebui/messages/capture",
        json={
            "session_uuid": session["session_uuid"],
            "role": "user",
            "content": "hello from Open WebUI",
            "idempotency_key": "daily-capture-1",
        },
    )
    status = (
        "ok"
        if first.status_code == 200
        and second.status_code == 200
        and second.json()["duplicate"] is True
        else "failed"
    )
    return {"name": "openwebui_capture", "status": status}


def _check_budget(client: TestClient) -> dict[str, Any]:
    response = client.post(
        "/memcore/budget/attachment",
        json={"target_model": "unknown-local-model", "recent_conversation_text": "short context"},
    )
    body = response.json()
    status = (
        "ok"
        if response.status_code == 200 and body["effective_token_budget"] >= 0
        else "failed"
    )
    return {"name": "budget", "status": status}


def _check_diagnostics(client: TestClient) -> dict[str, Any]:
    daily = client.get("/memcore/diagnostics/daily")
    actor = client.get("/memcore/diagnostics/write-actor")
    status = "ok" if daily.status_code == 200 and actor.status_code == 200 else "failed"
    return {"name": "diagnostics", "status": status}


if __name__ == "__main__":
    raise SystemExit(main())
