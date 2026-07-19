from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from starlette.routing import Mount
from starlette.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parents[1]
PARENT = ROOT.parent
if str(PARENT) not in sys.path:
    sys.path.insert(0, str(PARENT))

from memorist.backend.route_order import (  # noqa: E402
    MemoristRouteOrderError,
    assert_memorist_route_order,
    describe_route_order,
    ensure_memorist_routes_precede_spa,
    find_spa_mount_index,
)
from memorist.backend.router import router  # noqa: E402


def _spa_app(tmp_path: Path, *, mount_name: str = "spa-static-files") -> FastAPI:
    (tmp_path / "index.html").write_text("<html>SPA</html>", encoding="utf-8")
    app = FastAPI()

    @app.get("/health")
    def health() -> dict[str, bool]:
        return {"status": True}

    app.mount("/", StaticFiles(directory=tmp_path, html=True), name=mount_name)
    return app


def test_router_added_after_spa_is_shadowed_then_repaired(tmp_path: Path) -> None:
    app = _spa_app(tmp_path)
    app.include_router(router)

    report = describe_route_order(app)
    assert report.spa_mount_present
    assert not report.ordered_before_spa

    repaired = ensure_memorist_routes_precede_spa(app)
    assert repaired.ordered_before_spa
    assert repaired.memorist_route_count == len(router.routes)
    # Idempotent: applying again keeps the exact ordering.
    order_once = [id(route) for route in app.router.routes]
    ensure_memorist_routes_precede_spa(app)
    assert [id(route) for route in app.router.routes] == order_once

    assert_memorist_route_order(app, minimum_route_count=len(router.routes))


def test_reorder_fails_closed_on_unknown_root_mount(tmp_path: Path) -> None:
    app = _spa_app(tmp_path, mount_name="not-the-spa")
    app.include_router(router)
    with pytest.raises(MemoristRouteOrderError, match="not the known Open WebUI SPA mount"):
        ensure_memorist_routes_precede_spa(app)


def test_reorder_fails_closed_on_multiple_root_mounts(tmp_path: Path) -> None:
    app = _spa_app(tmp_path)
    app.router.routes.append(
        Mount("/", app=StaticFiles(directory=tmp_path, html=True), name="spa-static-files")
    )
    app.include_router(router)
    with pytest.raises(MemoristRouteOrderError, match="multiple root catch-all mounts"):
        ensure_memorist_routes_precede_spa(app)


def test_no_spa_mount_means_no_shadowing(tmp_path: Path) -> None:
    app = FastAPI()
    app.include_router(router)
    assert find_spa_mount_index(app.router.routes) is None
    report = ensure_memorist_routes_precede_spa(app)
    assert not report.spa_mount_present
    assert report.ordered_before_spa
    assert_memorist_route_order(app, minimum_route_count=len(router.routes))


def test_assertion_fails_loudly_when_routes_follow_spa(tmp_path: Path) -> None:
    app = _spa_app(tmp_path)
    app.include_router(router)
    with pytest.raises(MemoristRouteOrderError, match="unreachable"):
        assert_memorist_route_order(app, minimum_route_count=len(router.routes))


def test_assertion_fails_when_routes_are_missing(tmp_path: Path) -> None:
    app = _spa_app(tmp_path)
    with pytest.raises(MemoristRouteOrderError):
        assert_memorist_route_order(app, minimum_route_count=len(router.routes))
