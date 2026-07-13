from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
PARENT = ROOT.parent
if str(PARENT) not in sys.path:
    sys.path.insert(0, str(PARENT))


class FakeCoreClient:
    calls: list[dict[str, Any]] = []

    def actor_request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        self.calls.append({"method": method, "path": path, **kwargs})
        return {"ok": True, "actor": kwargs["user_id"]}


def _app(*, authenticated: bool) -> TestClient:
    module = importlib.import_module("memorist.backend.router")
    module.MemoristClient = FakeCoreClient
    app = FastAPI()

    @app.middleware("http")
    async def trusted_session(request: Request, call_next: Any) -> Any:
        if authenticated:
            request.state.memorist_actor = SimpleNamespace(
                user_uuid="trusted-user", workspace_uuid="trusted-workspace"
            )
        return await call_next(request)

    app.include_router(module.router)
    return TestClient(app)


def test_proxy_rejects_raw_forged_actor_headers_without_server_session() -> None:
    response = _app(authenticated=False).get(
        "/api/v1/memorist/memory-control/attachments/a/preview",
        headers={
            "X-Memorist-User-Id": "victim",
            "X-Memorist-Workspace-Id": "victim-workspace",
        },
    )
    assert response.status_code == 401


def test_proxy_overrides_browser_identity_and_signs_as_server_actor() -> None:
    FakeCoreClient.calls = []
    response = _app(authenticated=True).post(
        "/api/v1/memorist/memory-control/policy/resolve",
        json={
            "user_uuid": "victim",
            "workspace_uuid": "victim-workspace",
            "memorist": {"turn_policy": "full"},
        },
    )
    assert response.status_code == 200
    call = FakeCoreClient.calls[-1]
    assert call["user_id"] == "trusted-user"
    assert call["workspace_uuid"] == "trusted-workspace"
    assert call["payload"]["user_uuid"] == "trusted-user"
    assert call["payload"]["workspace_uuid"] == "trusted-workspace"
