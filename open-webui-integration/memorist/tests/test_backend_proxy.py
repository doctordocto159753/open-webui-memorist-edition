from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import yaml
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
PARENT = ROOT.parent
if str(PARENT) not in sys.path:
    sys.path.insert(0, str(PARENT))


class FakeCoreClient:
    calls: list[dict[str, Any]] = []
    policy_mode = "full"

    def actor_request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        self.calls.append({"method": method, "path": path, **kwargs})
        return {"ok": True, "actor": kwargs["user_id"]}

    def status(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append({"operation": "status", **kwargs})
        return {"memorist_core": "connected"}

    def resolve_turn_policy(self, **kwargs: Any) -> SimpleNamespace:
        self.calls.append({"operation": "policy", **kwargs})
        mode = self.policy_mode
        return SimpleNamespace(
            mode=mode,
            private=mode == "private",
            recall_enabled=mode == "full",
            attachment_enabled=mode == "full",
        )

    def resolve_session(self, *_args: Any, **kwargs: Any) -> SimpleNamespace:
        self.calls.append({"operation": "session", **kwargs})
        return SimpleNamespace(session_uuid="session-1")

    def capture_message(self, *_args: Any, **kwargs: Any) -> SimpleNamespace:
        self.calls.append({"operation": "capture", **kwargs})
        return SimpleNamespace(message_uuid="message-1")

    def preflight(self, *_args: Any, **kwargs: Any) -> SimpleNamespace:
        self.calls.append({"operation": "preflight", **kwargs})
        return SimpleNamespace(status="attached", attachment_uuid="attachment-1")


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


def test_attachment_display_proxy_uses_trusted_actor_and_safe_endpoint() -> None:
    FakeCoreClient.calls = []
    response = _app(authenticated=True).get(
        "/api/v1/memorist/memory-control/attachments/attachment-1/display"
    )

    assert response.status_code == 200
    call = FakeCoreClient.calls[-1]
    assert call["method"] == "GET"
    assert call["path"] == "/memcore/memory-control/attachments/attachment-1/display"
    assert call["user_id"] == "trusted-user"
    assert call["workspace_uuid"] == "trusted-workspace"


def test_review_prepare_uses_trusted_actor_and_preserves_message_identity() -> None:
    FakeCoreClient.calls = []
    response = _app(authenticated=True).post(
        "/api/v1/memorist/memory-control/review/prepare",
        json={
            "conversation_id": "chat-1",
            "message_id": "browser-message-1",
            "content": "remember this",
            "user_uuid": "victim",
            "workspace_uuid": "victim-workspace",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["attachment_uuid"] == "attachment-1"
    assert response.json()["input_message_uuid"] == "message-1"
    capture = next(
        call for call in FakeCoreClient.calls if call.get("operation") == "capture"
    )
    assert capture["user_id"] == "trusted-user"
    assert capture["workspace_uuid"] == "trusted-workspace"
    assert capture["openwebui_message_id"] == "browser-message-1"


def test_review_prepare_respects_effective_private_and_no_recall_policy() -> None:
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
        policy_call = next(
            call for call in FakeCoreClient.calls if call.get("operation") == "policy"
        )
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
    policy_call = next(
        call for call in FakeCoreClient.calls if call.get("operation") == "policy"
    )
    assert policy_call["request_control"] == {
        "attachment_review": True,
        "turn_policy": "full",
    }


def test_shipped_entrypoint_mounts_router_with_verified_openwebui_user(
    monkeypatch: Any,
) -> None:
    import types

    app = FastAPI()

    def get_verified_user() -> SimpleNamespace:
        return SimpleNamespace(id="verified-openwebui-user")

    open_webui = types.ModuleType("open_webui")
    main = types.ModuleType("open_webui.main")
    auth = types.ModuleType("open_webui.utils.auth")
    utils = types.ModuleType("open_webui.utils")
    main.app = app
    auth.get_verified_user = get_verified_user
    monkeypatch.setitem(sys.modules, "open_webui", open_webui)
    monkeypatch.setitem(sys.modules, "open_webui.main", main)
    monkeypatch.setitem(sys.modules, "open_webui.utils", utils)
    monkeypatch.setitem(sys.modules, "open_webui.utils.auth", auth)
    monkeypatch.setenv(
        "MEMORIST_OPENWEBUI_WORKSPACE_UUID", "00000000-0000-0000-0000-000000000001"
    )
    entrypoint = importlib.import_module("memorist.backend.openwebui_entrypoint")
    mounted = entrypoint.create_app()

    assert mounted is app
    assert entrypoint.require_openwebui_actor in app.dependency_overrides
    response = TestClient(app).get("/api/v1/memorist/openwebui/status")
    assert response.status_code == 200, response.text
    assert response.json()["memorist_core"] == "connected"


def test_release_compose_keeps_core_command_and_mounts_authenticated_ui_router() -> (
    None
):
    compose_path = ROOT.parents[1] / "release" / "memorist-openwebui" / "compose.yml"
    compose = yaml.safe_load(compose_path.read_text(encoding="utf-8-sig"))
    core = compose["services"]["memorist-core"]
    webui = compose["services"]["open-webui"]

    assert "command" not in core
    assert "/memcore/health" in " ".join(core["healthcheck"]["test"])
    assert webui["command"] == ["python", "-m", "memorist.backend.openwebui_entrypoint"]
    assert webui["environment"]["PYTHONPATH"] == "/memorist-integration"
    assert "MEMORIST_OPENWEBUI_WORKSPACE_UUID" in webui["environment"]
    assert any(
        str(volume).endswith(":/memorist-integration/memorist:ro")
        for volume in webui["volumes"]
    )
