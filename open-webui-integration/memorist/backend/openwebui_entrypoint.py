"""Production entrypoint mounting Memorist inside the pinned Open WebUI backend.

This module is the single supported way to run the Memorist edition of
Open WebUI. It:

1. verifies the imported Open WebUI version is one the integration was
   certified against (fail-closed, explicit override env for operators);
2. binds the Memorist actor dependency to Open WebUI's native session
   authentication (``get_verified_user``) — browser-controlled identity is
   never consulted;
3. registers the authenticated proxy routers and deterministically reorders
   them ahead of the SPA catch-all mount (see ``route_order``);
4. wraps the upstream lifespan so that, after Open WebUI's own startup, the
   managed Memorist chat filter is provisioned and the route-order invariant
   is asserted — any violation aborts startup loudly.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

from fastapi import Depends

from .filter_provisioning import ensure_memorist_filter_provisioned
from .import_proxy import router as import_router
from .route_order import (
    assert_memorist_route_order,
    ensure_memorist_routes_precede_spa,
)
from .router import OpenWebUIActor, require_openwebui_actor, router

SUPPORTED_OPENWEBUI_VERSIONS = ("0.9.6",)
_VERSION_OVERRIDE_ENV = "MEMORIST_ALLOW_OPENWEBUI_VERSION"


class MemoristHostVersionError(RuntimeError):
    """The imported Open WebUI version is not certified for this integration."""


def detect_openwebui_versions() -> tuple[str, ...]:
    """Collect every version signal the pinned host exposes.

    The container image reads its version from ``package.json`` (via
    ``open_webui.env.VERSION``) while a pip-installed host only knows its
    distribution metadata; either signal may degrade to a placeholder in the
    other layout, so both are considered.
    """
    versions: list[str] = []
    try:
        from importlib.metadata import version as distribution_version

        versions.append(str(distribution_version("open-webui")))
    except Exception:
        pass
    try:
        from open_webui.env import VERSION as env_version

        versions.append(str(env_version))
    except Exception:
        pass
    return tuple(dict.fromkeys(versions))


def assert_supported_openwebui_version() -> str:
    detected = detect_openwebui_versions()
    for version in detected:
        if version in SUPPORTED_OPENWEBUI_VERSIONS:
            return version
        if version and os.environ.get(_VERSION_OVERRIDE_ENV, "") == version:
            return version
    raise MemoristHostVersionError(
        f"Open WebUI {' / '.join(detected) or 'unknown'} is not a certified Memorist host "
        f"(certified: {', '.join(SUPPORTED_OPENWEBUI_VERSIONS)}). Set "
        f"{_VERSION_OVERRIDE_ENV}=<version> only after re-running the "
        "integration acceptance gates against that version."
    )


def _expected_memorist_route_count() -> int:
    return len(router.routes) + len(import_router.routes)


def create_app() -> Any:
    """Mount Memorist into the pinned Open WebUI backend with native session auth."""
    assert_supported_openwebui_version()

    from open_webui.main import app
    from open_webui.utils.auth import get_verified_user

    workspace_uuid = str(UUID(os.environ["MEMORIST_OPENWEBUI_WORKSPACE_UUID"]))
    verified_user_dependency = Depends(get_verified_user)

    def verified_memorist_actor(user: Any = verified_user_dependency) -> OpenWebUIActor:
        role = str(getattr(user, "role", "")).lower()
        is_admin = bool(getattr(user, "is_admin", False)) or role in {"admin", "owner"}
        return OpenWebUIActor(
            user_uuid=str(user.id),
            workspace_uuid=workspace_uuid,
            is_admin=is_admin,
        )

    app.dependency_overrides[require_openwebui_actor] = verified_memorist_actor
    if not any(
        str(getattr(route, "path", "")).startswith("/api/v1/memorist/")
        and not str(getattr(route, "path", "")).startswith("/api/v1/memorist/imports")
        for route in app.routes
    ):
        app.include_router(router)
    if not any(
        str(getattr(route, "path", "")).startswith("/api/v1/memorist/imports")
        for route in app.routes
    ):
        app.include_router(import_router)
    ensure_memorist_routes_precede_spa(app)
    assert_memorist_route_order(app, minimum_route_count=_expected_memorist_route_count())
    _install_memorist_lifespan(app)
    return app


_MEMORIST_LIFESPAN_FLAG = "_memorist_lifespan_installed"


def _install_memorist_lifespan(app: Any) -> None:
    """Run Memorist startup guarantees after Open WebUI's own startup completes."""
    if getattr(app.state, _MEMORIST_LIFESPAN_FLAG, False):
        return
    upstream_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def memorist_lifespan(host_app: Any) -> AsyncIterator[Any]:
        async with upstream_lifespan(host_app) as state:
            summary = await ensure_memorist_filter_provisioned()
            report = assert_memorist_route_order(
                host_app, minimum_route_count=_expected_memorist_route_count()
            )
            host_app.state.memorist_integration = {
                "filter": summary,
                "route_order": {
                    "spa_mount_present": report.spa_mount_present,
                    "memorist_route_count": report.memorist_route_count,
                    "ordered_before_spa": report.ordered_before_spa,
                },
            }
            yield state

    app.router.lifespan_context = memorist_lifespan
    setattr(app.state, _MEMORIST_LIFESPAN_FLAG, True)


def main() -> None:
    import uvicorn

    uvicorn.run(
        "memorist.backend.openwebui_entrypoint:create_app",
        factory=True,
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8080")),
        workers=int(os.getenv("WEB_CONCURRENCY", "1")),
        forwarded_allow_ips=os.getenv("FORWARDED_ALLOW_IPS", "*"),
    )


if __name__ == "__main__":
    main()
