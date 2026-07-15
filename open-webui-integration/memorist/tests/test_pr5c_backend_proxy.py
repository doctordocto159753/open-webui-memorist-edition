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
        if path.endswith("/setup/status"):
            return {"ready_for_memory_processing": True}
        if path.endswith("/profiles") and method == "POST":
            return {
                "model_profile_uuid": "profile-1",
                "secret_configured": True,
            }
        return {"ok": True}


def _app(*, authenticated: bool, admin: bool = False) -> TestClient:
    module = importlib.import_module("memorist.backend.router")
    module.MemoristClient = FakeCoreClient
    app = FastAPI()

    @app.middleware("http")
    async def trusted_session(request: Request, call_next: Any) -> Any:
        if authenticated:
            request.state.memorist_actor = SimpleNamespace(
                user_uuid="trusted-user",
                workspace_uuid="trusted-workspace",
                is_admin=admin,
            )
        return await call_next(request)

    app.include_router(module.router)
    return TestClient(app)


def test_setup_proxy_requires_authenticated_openwebui_admin() -> None:
    unauthenticated = _app(authenticated=False).get(
        "/api/v1/memorist/model-control/setup/status"
    )
    assert unauthenticated.status_code == 401

    non_admin = _app(authenticated=True, admin=False).get(
        "/api/v1/memorist/model-control/setup/status"
    )
    assert non_admin.status_code == 403

    admin = _app(authenticated=True, admin=True).get(
        "/api/v1/memorist/model-control/setup/status"
    )
    assert admin.status_code == 200


def test_setup_status_is_actor_scoped_and_admin_default_rejects_browser_scope() -> None:
    FakeCoreClient.calls = []
    client = _app(authenticated=True, admin=True)

    status = client.get("/api/v1/memorist/model-control/setup/status")
    assert status.status_code == 200
    status_call = FakeCoreClient.calls[-1]
    assert status_call["path"].endswith("workspace_uuid=trusted-workspace")
    assert status_call["user_id"] == "trusted-user"
    assert status_call["workspace_uuid"] == "trusted-workspace"

    selected = client.post(
        "/api/v1/memorist/model-control/defaults",
        json={
            "role": "memory_extraction",
            "model_profile_uuid": "profile-1",
            "workspace_uuid": "victim-workspace",
            "project_uuid": "victim-project",
        },
    )
    assert selected.status_code == 200
    default_call = FakeCoreClient.calls[-1]
    assert "workspace_uuid" not in default_call["payload"]
    assert "project_uuid" not in default_call["payload"]


def test_profile_proxy_forwards_env_reference_but_never_echoes_secret() -> None:
    FakeCoreClient.calls = []
    client = _app(authenticated=True, admin=True)
    secret_value = "provider-value-must-not-appear"
    response = client.post(
        "/api/v1/memorist/model-control/profiles",
        json={
            "provider_type": "openai_compatible_llm",
            "model_name": "example-memory-model",
            "role": "memory_extraction",
            "endpoint_url": "https://provider.example/v1",
            "secret_strategy": "env_var",
            "secret_env_var_name": "MEMORIST_MEMORY_EXTRACTION_API_KEY",
        },
    )
    assert response.status_code == 200
    assert response.json()["secret_configured"] is True
    assert secret_value not in response.text
    call = FakeCoreClient.calls[-1]
    assert call["payload"]["secret_env_var_name"] == "MEMORIST_MEMORY_EXTRACTION_API_KEY"


def test_profile_proxy_rejects_raw_or_nested_secret_fields_before_forwarding() -> None:
    client = _app(authenticated=True, admin=True)
    for payload in (
        {
            "role": "memory_extraction",
            "model_name": "example",
            "api_key": "raw-provider-value",
        },
        {
            "role": "memory_extraction",
            "model_name": "example",
            "metadata": {"access_token": "raw-provider-value"},
        },
    ):
        FakeCoreClient.calls = []
        response = client.post(
            "/api/v1/memorist/model-control/profiles",
            json=payload,
        )
        assert response.status_code == 422
        assert "raw-provider-value" not in response.text
        assert FakeCoreClient.calls == []
