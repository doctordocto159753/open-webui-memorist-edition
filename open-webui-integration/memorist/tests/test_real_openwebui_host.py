"""Acceptance tests against the real imported Open WebUI application.

These tests intentionally do NOT build an isolated FastAPI app. They import
the production entrypoint (``memorist.backend.openwebui_entrypoint``), which
imports ``open_webui.main`` — the same application object the shipped
container serves — with a real SPA build directory mounted, real session
authentication, and the real Functions table for filter provisioning.

Skipped automatically when ``open_webui`` is not installed; export
``MEMORIST_REQUIRE_REAL_OPENWEBUI=1`` (CI does) to turn that skip into a hard
failure so the gate cannot silently degrade.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
PARENT = ROOT.parent
if str(PARENT) not in sys.path:
    sys.path.insert(0, str(PARENT))

WORKSPACE_UUID = "123e4567-e89b-42d3-a456-426614174000"

_HAS_OPENWEBUI = importlib.util.find_spec("open_webui") is not None
_REQUIRED = os.environ.get("MEMORIST_REQUIRE_REAL_OPENWEBUI") == "1"

if not _HAS_OPENWEBUI and _REQUIRED:
    raise RuntimeError(
        "MEMORIST_REQUIRE_REAL_OPENWEBUI=1 but the open_webui package is not "
        "installed; the real-host gate must not silently skip"
    )

pytestmark = pytest.mark.skipif(
    not _HAS_OPENWEBUI, reason="open_webui is not installed in this environment"
)


class FakeCoreClient:
    """Stands in for Memorist Core transport only; auth/routing stay real."""

    calls: list[dict[str, Any]] = []

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    def actor_request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        FakeCoreClient.calls.append({"method": method, "path": path, **kwargs})
        return {"ok": True, "path": path, "actor": kwargs.get("user_id")}

    def status(self, **kwargs: Any) -> dict[str, Any]:
        FakeCoreClient.calls.append({"operation": "status", **kwargs})
        return {"memorist_core": "connected"}

    def health(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "service": "memorist-core",
            "runtime_profile": "full",
            "canonical_store": "postgres",
            "graph_backend": "falkordb",
            "graph_status": "ok",
            "scheduler": "in_memory",
            "profile_warnings": [],
            "full_mode_certification": "not_run",
            "postgres_dsn": "postgresql://memorist:SECRET@postgres/memorist",
        }

    def resolve_turn_policy(self, **kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(
            mode="full",
            private=False,
            recall_enabled=True,
            attachment_enabled=True,
        )


@pytest.fixture(scope="module")
def host() -> Any:
    data_dir = tempfile.mkdtemp(prefix="memorist-owui-host-")
    frontend_dir = Path(tempfile.mkdtemp(prefix="memorist-owui-frontend-"))
    (frontend_dir / "index.html").write_text("<html>Memorist SPA stub</html>", encoding="utf-8")
    os.environ.setdefault("DATA_DIR", data_dir)
    os.environ.setdefault("FRONTEND_BUILD_DIR", str(frontend_dir))
    os.environ.setdefault("WEBUI_SECRET_KEY", "memorist-test-secret")
    os.environ.setdefault("RAG_EMBEDDING_ENGINE", "ollama")
    os.environ.setdefault("DEFAULT_USER_ROLE", "user")
    os.environ["MEMORIST_OPENWEBUI_WORKSPACE_UUID"] = WORKSPACE_UUID

    import importlib

    # NOTE: ``memorist.backend.__init__`` re-exports the APIRouter under the
    # name ``router``, shadowing the submodule attribute; import_module returns
    # the real module object so the patch reaches the endpoints' globals.
    router_module = importlib.import_module("memorist.backend.router")
    from memorist.backend.openwebui_entrypoint import create_app

    router_module.MemoristClient = FakeCoreClient

    from starlette.testclient import TestClient

    app = create_app()
    with TestClient(app) as client:
        signup = client.post(
            "/api/v1/auths/signup",
            json={"name": "Admin", "email": "admin@example.com", "password": "memorist-admin-pass"},
        )
        assert signup.status_code == 200, signup.text
        admin_token = signup.json()["token"]

        created = client.post(
            "/api/v1/auths/add",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "name": "Member",
                "email": "member@example.com",
                "password": "memorist-member-pass",
                "role": "user",
            },
        )
        assert created.status_code == 200, created.text
        member = client.post(
            "/api/v1/auths/signin",
            json={"email": "member@example.com", "password": "memorist-member-pass"},
        )
        assert member.status_code == 200, member.text
        member_token = member.json()["token"]
        # Signup/signin set session cookies on the shared TestClient; identity
        # in these tests must come only from explicit Authorization headers.
        client.cookies.clear()

        yield SimpleNamespace(
            app=app,
            client=client,
            admin_headers={"Authorization": f"Bearer {admin_token}"},
            member_headers={"Authorization": f"Bearer {member_token}"},
        )


def test_unauthenticated_memorist_status_is_401_json_not_spa(host: Any) -> None:
    response = host.client.get("/api/v1/memorist/openwebui/status")
    assert response.status_code == 401
    assert response.headers["content-type"].startswith("application/json")
    assert "SPA" not in response.text


def test_known_memorist_paths_never_return_404(host: Any) -> None:
    for path in (
        "/api/v1/memorist/openwebui/status",
        "/api/v1/memorist/openwebui/diagnostics",
        "/api/v1/memorist/model-control/setup/status",
        "/api/v1/memorist/model-control/roles",
        "/api/v1/memorist/model-control/profiles",
        "/api/v1/memorist/memory-control/workflow/chats/some-chat",
        "/api/v1/memorist/memory-control/messages/some-message/attachment",
    ):
        response = host.client.get(path)
        assert response.status_code == 401, (path, response.status_code, response.text[:120])
        assert response.headers["content-type"].startswith("application/json")


def test_authenticated_member_reaches_status(host: Any) -> None:
    response = host.client.get(
        "/api/v1/memorist/openwebui/status", headers=host.member_headers
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"memorist_core": "connected"}


def test_member_is_denied_admin_setup_status(host: Any) -> None:
    response = host.client.get(
        "/api/v1/memorist/model-control/setup/status", headers=host.member_headers
    )
    assert response.status_code == 403


def test_admin_reaches_setup_status(host: Any) -> None:
    response = host.client.get(
        "/api/v1/memorist/model-control/setup/status", headers=host.admin_headers
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["ok"] is True
    assert payload["path"].startswith("/memcore/model-control/setup/status")


def test_admin_diagnostics_is_safe_and_truthful(host: Any) -> None:
    response = host.client.get(
        "/api/v1/memorist/openwebui/diagnostics", headers=host.admin_headers
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["core_health"]["runtime_profile"] == "full"
    assert payload["core_health"]["full_mode_certification"] == "not_run"
    assert "not a runtime failure" in payload["core_health"]["full_mode_certification_note"]
    assert "postgres_dsn" not in payload["core_health"]
    assert "SECRET" not in response.text
    assert payload["openwebui_version"] != "unknown"
    assert payload["integration"]["route_order"]["ordered_before_spa"] is True
    member = host.client.get(
        "/api/v1/memorist/openwebui/diagnostics", headers=host.member_headers
    )
    assert member.status_code == 403


def test_member_diagnostics_denied_but_workflow_allowed(host: Any) -> None:
    response = host.client.get(
        "/api/v1/memorist/memory-control/workflow/chats/chat-1", headers=host.member_headers
    )
    assert response.status_code == 200


def test_route_order_invariant_on_real_app(host: Any) -> None:
    from starlette.routing import Mount

    routes = host.app.router.routes
    spa_indexes = [
        index
        for index, route in enumerate(routes)
        if isinstance(route, Mount) and getattr(route, "name", None) == "spa-static-files"
    ]
    assert len(spa_indexes) == 1
    memorist_indexes = [
        index
        for index, route in enumerate(routes)
        if str(getattr(route, "path", "")).startswith("/api/v1/memorist")
    ]
    assert memorist_indexes, "memorist routes missing from the real application"
    assert max(memorist_indexes) < spa_indexes[0]


def test_spa_and_health_still_work(host: Any) -> None:
    assert host.client.get("/health").status_code == 200
    root = host.client.get("/")
    assert root.status_code == 200
    assert "Memorist SPA stub" in root.text


def test_filter_provisioned_in_real_functions_table(host: Any) -> None:
    import asyncio

    from open_webui.models.functions import Functions

    from memorist.backend.filter_provisioning import (
        MEMORIST_FILTER_CONTENT_VERSION,
        MEMORIST_FILTER_ID,
        ensure_memorist_filter_provisioned,
    )

    async def scenario() -> tuple[Any, dict[str, Any]]:
        function = await Functions.get_function_by_id(MEMORIST_FILTER_ID)
        rerun = await ensure_memorist_filter_provisioned()
        return function, rerun

    function, rerun = asyncio.get_event_loop().run_until_complete(scenario())
    assert function is not None, "startup did not provision the Memorist filter"
    assert function.type == "filter"
    assert function.is_active is True
    assert function.is_global is True
    assert MEMORIST_FILTER_CONTENT_VERSION in function.content
    assert rerun == {"filter_id": MEMORIST_FILTER_ID, "action": "unchanged"}


def test_filter_provisioning_repairs_tampered_row(host: Any) -> None:
    import asyncio

    from open_webui.models.functions import Functions

    from memorist.backend.filter_provisioning import (
        MEMORIST_FILTER_ID,
        ensure_memorist_filter_provisioned,
    )

    async def scenario() -> tuple[dict[str, Any], Any]:
        await Functions.update_function_by_id(
            MEMORIST_FILTER_ID, {"is_active": False, "content": "tampered"}
        )
        outcome = await ensure_memorist_filter_provisioned()
        repaired = await Functions.get_function_by_id(MEMORIST_FILTER_ID)
        return outcome, repaired

    outcome, repaired = asyncio.get_event_loop().run_until_complete(scenario())
    assert outcome["action"] == "updated"
    assert repaired.is_active is True and repaired.is_global is True
    assert "tampered" not in repaired.content


def test_unsupported_openwebui_version_fails_closed(host: Any, monkeypatch: Any) -> None:
    from memorist.backend import openwebui_entrypoint

    monkeypatch.setattr(
        openwebui_entrypoint, "SUPPORTED_OPENWEBUI_VERSIONS", ("0.0.0-never",)
    )
    monkeypatch.delenv("MEMORIST_ALLOW_OPENWEBUI_VERSION", raising=False)
    with pytest.raises(openwebui_entrypoint.MemoristHostVersionError):
        openwebui_entrypoint.assert_supported_openwebui_version()
