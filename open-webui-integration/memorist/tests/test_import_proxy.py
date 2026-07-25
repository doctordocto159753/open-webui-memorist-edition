from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from memorist.backend import import_proxy
from memorist.backend.filter_provisioning import managed_filter_content
from memorist.backend.router import OpenWebUIActor, require_openwebui_actor
from memorist.filter.memorist_memory_filter import Filter as PackagedFilter


class FakeResponse:
    status_code = 200
    is_success = True
    content = b'{"ok":true}'

    def json(self) -> dict[str, Any]:
        return {"ok": True}


class FakeAsyncClient:
    calls: list[dict[str, Any]] = []
    timeouts: list[float] = []

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.timeouts.append(float(kwargs["timeout"]))

    async def __aenter__(self) -> FakeAsyncClient:
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    async def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"kind": "request", "method": method, "url": url, **kwargs})
        return FakeResponse()

    async def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"kind": "upload", "method": "POST", "url": url, **kwargs})
        return FakeResponse()


class FakeMemoristClient:
    actor_calls: list[tuple[str, str, str, str]] = []

    def __init__(self) -> None:
        self.base_url = "http://memorist-core:8777"
        self.config = SimpleNamespace(
            timeout_seconds_for=lambda path: 300.0
            if path.startswith("/memcore/imports")
            else 1.2
        )

    def _actor_headers(
        self,
        method: str,
        path: str,
        user_uuid: str,
        workspace_uuid: str,
    ) -> dict[str, str]:
        self.actor_calls.append((method, path, user_uuid, workspace_uuid))
        return {"X-Memorist-Test-Actor": user_uuid}


def app_for(actor: OpenWebUIActor | None) -> TestClient:
    app = FastAPI()
    app.include_router(import_proxy.router)
    if actor is not None:
        app.dependency_overrides[require_openwebui_actor] = lambda: actor
    return TestClient(app)


def test_import_proxy_requires_verified_admin() -> None:
    assert app_for(None).get("/api/v1/memorist/imports").status_code == 401
    member = OpenWebUIActor("member", "workspace", False)
    assert app_for(member).get("/api/v1/memorist/imports").status_code == 403


def test_import_proxy_forwards_only_after_admin_auth(monkeypatch: Any) -> None:
    FakeAsyncClient.calls.clear()
    FakeAsyncClient.timeouts.clear()
    FakeMemoristClient.actor_calls.clear()
    monkeypatch.setattr(import_proxy, "MemoristClient", FakeMemoristClient)
    monkeypatch.setattr(import_proxy.httpx, "AsyncClient", FakeAsyncClient)
    admin = OpenWebUIActor("admin-user", "server-workspace", True)

    response = app_for(admin).get("/api/v1/memorist/imports?limit=10")

    assert response.status_code == 200
    assert FakeMemoristClient.actor_calls == [
        ("GET", "/memcore/imports", "admin-user", "server-workspace")
    ]
    assert FakeAsyncClient.calls[0]["url"] == "http://memorist-core:8777/memcore/imports"
    assert FakeAsyncClient.calls[0]["params"] == [("limit", "10")]
    assert FakeAsyncClient.timeouts == [300.0]


def test_import_upload_forces_server_scope(monkeypatch: Any) -> None:
    FakeAsyncClient.calls.clear()
    FakeAsyncClient.timeouts.clear()
    FakeMemoristClient.actor_calls.clear()
    monkeypatch.setattr(import_proxy, "MemoristClient", FakeMemoristClient)
    monkeypatch.setattr(import_proxy.httpx, "AsyncClient", FakeAsyncClient)
    admin = OpenWebUIActor("admin-user", "server-workspace", True)

    response = app_for(admin).post(
        "/api/v1/memorist/imports/upload-file",
        data={
            "mode": "inspect",
            "processing_mode": "none",
            "target_workspace_uuid": "attacker-workspace",
        },
        files={"file": ("export.json", b"{}", "application/json")},
    )

    assert response.status_code == 200
    upload = FakeAsyncClient.calls[0]
    assert upload["data"]["target_workspace_uuid"] == "server-workspace"
    assert "attacker-workspace" not in str(upload)
    assert FakeMemoristClient.actor_calls == [
        ("POST", "/memcore/imports/upload-file", "admin-user", "server-workspace")
    ]
    assert FakeAsyncClient.timeouts == [300.0]


def test_managed_filter_overwrites_browser_workspace(monkeypatch: Any) -> None:
    monkeypatch.setenv("MEMORIST_OPENWEBUI_WORKSPACE_UUID", "server-workspace")
    namespace: dict[str, Any] = {}
    exec(managed_filter_content(), namespace)

    actor = namespace["_server_side_user"](
        {"id": "user-1", "workspace_id": "browser-controlled-workspace"}
    )

    assert actor["workspace_id"] == "server-workspace"


def test_managed_filter_forwards_trusted_host_metadata(monkeypatch: Any) -> None:
    monkeypatch.setenv("MEMORIST_OPENWEBUI_WORKSPACE_UUID", "server-workspace")
    received: dict[str, Any] = {}

    def fake_inlet(
        _self: Any,
        body: dict[str, Any],
        __user__: dict[str, Any] | None = None,
        __metadata__: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        received["user"] = __user__
        received["metadata"] = __metadata__
        return body

    monkeypatch.setattr(PackagedFilter, "inlet", fake_inlet)
    namespace: dict[str, Any] = {}
    exec(managed_filter_content(), namespace)

    result = namespace["Filter"]().inlet(
        {"messages": []},
        __user__={"id": "user-1", "workspace_id": "browser-workspace"},
        __metadata__={"chat_id": "trusted-chat-id"},
    )

    assert result == {"messages": []}
    assert received["user"] == {
        "id": "user-1",
        "workspace_id": "server-workspace",
    }
    assert received["metadata"] == {"chat_id": "trusted-chat-id"}
