"""Deterministic, fail-closed route ordering for the Memorist proxy.

Open WebUI mounts its single-page-application static handler at ``/`` while the
module is imported. Any router added afterwards is appended *after* that
catch-all mount, so Starlette never reaches it: ``/api/v1/memorist/*`` is
swallowed by the SPA handler and the product looks healthy while the Memorist
API is unreachable. These helpers repair and then permanently assert the
ordering invariant against the real imported application.

Rules implemented here (PR5-G contract):

* The upstream SPA mount is identified only by the stable combination of
  route type (``starlette.routing.Mount``), path (``""``/``"/"`` — Starlette
  normalizes a ``"/"`` mount to ``""``), and name (``spa-static-files``).
* If a root catch-all mount exists that does NOT match that identity, the
  repair fails closed instead of guessing.
* The reorder is deterministic and idempotent: Memorist routes keep their
  relative order and are placed immediately before the SPA mount.
* ``assert_memorist_route_order`` raises ``MemoristRouteOrderError`` so a
  misordered application fails loudly at startup instead of serving a
  superficially healthy but disconnected product.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from starlette.routing import Mount

MEMORIST_ROUTE_PREFIX = "/api/v1/memorist"
SPA_MOUNT_NAME = "spa-static-files"
_ROOT_MOUNT_PATHS = {"", "/"}


class MemoristRouteOrderError(RuntimeError):
    """Startup-fatal violation of the Memorist route ordering contract."""


@dataclass(frozen=True)
class RouteOrderReport:
    spa_mount_present: bool
    spa_mount_index: int | None
    memorist_route_count: int
    memorist_first_index: int | None
    memorist_last_index: int | None

    @property
    def ordered_before_spa(self) -> bool:
        if not self.spa_mount_present:
            return True
        if self.memorist_last_index is None or self.spa_mount_index is None:
            return False
        return self.memorist_last_index < self.spa_mount_index


def _is_memorist_route(route: Any) -> bool:
    path = getattr(route, "path", None)
    return isinstance(path, str) and path.startswith(MEMORIST_ROUTE_PREFIX)


def _is_root_mount(route: Any) -> bool:
    return isinstance(route, Mount) and getattr(route, "path", None) in _ROOT_MOUNT_PATHS


def find_spa_mount_index(routes: list[Any]) -> int | None:
    """Locate the upstream SPA mount, failing closed on any ambiguity.

    Returns ``None`` when no root catch-all mount exists (API-only mode: no
    route can be shadowed, so no repair is needed). Raises when a root mount
    exists that cannot be positively identified as the known SPA mount, or
    when more than one root mount exists.
    """
    root_mounts = [(index, route) for index, route in enumerate(routes) if _is_root_mount(route)]
    if not root_mounts:
        return None
    if len(root_mounts) > 1:
        raise MemoristRouteOrderError(
            "multiple root catch-all mounts found; refusing to reorder routes "
            f"(names: {[getattr(route, 'name', None) for _, route in root_mounts]})"
        )
    index, route = root_mounts[0]
    if getattr(route, "name", None) != SPA_MOUNT_NAME:
        raise MemoristRouteOrderError(
            "found a root catch-all mount that is not the known Open WebUI SPA mount "
            f"(name={getattr(route, 'name', None)!r}); refusing to reorder routes"
        )
    return index


def ensure_memorist_routes_precede_spa(app: Any) -> RouteOrderReport:
    """Deterministically and idempotently place Memorist routes before the SPA mount."""
    routes = app.router.routes
    spa_index = find_spa_mount_index(routes)
    memorist_routes = [route for route in routes if _is_memorist_route(route)]
    if not memorist_routes:
        raise MemoristRouteOrderError(
            f"no {MEMORIST_ROUTE_PREFIX} routes are registered on the application"
        )
    if spa_index is not None and any(
        index > spa_index for index, route in enumerate(routes) if _is_memorist_route(route)
    ):
        others = [route for route in routes if not _is_memorist_route(route)]
        spa_position = find_spa_mount_index(others)
        if spa_position is None:  # pragma: no cover - spa_index proved it exists
            raise MemoristRouteOrderError("SPA mount disappeared during reorder")
        reordered = others[:spa_position] + memorist_routes + others[spa_position:]
        # Mutate in place: FastAPI/Starlette share this exact list object.
        routes[:] = reordered
    return describe_route_order(app)


def describe_route_order(app: Any) -> RouteOrderReport:
    routes = app.router.routes
    spa_index = find_spa_mount_index(routes)
    memorist_indexes = [index for index, route in enumerate(routes) if _is_memorist_route(route)]
    return RouteOrderReport(
        spa_mount_present=spa_index is not None,
        spa_mount_index=spa_index,
        memorist_route_count=len(memorist_indexes),
        memorist_first_index=min(memorist_indexes) if memorist_indexes else None,
        memorist_last_index=max(memorist_indexes) if memorist_indexes else None,
    )


def assert_memorist_route_order(app: Any, *, minimum_route_count: int) -> RouteOrderReport:
    """Fail startup loudly unless every Memorist route precedes the SPA mount."""
    report = describe_route_order(app)
    if report.memorist_route_count < minimum_route_count:
        raise MemoristRouteOrderError(
            f"expected at least {minimum_route_count} Memorist routes, "
            f"found {report.memorist_route_count}"
        )
    if not report.ordered_before_spa:
        raise MemoristRouteOrderError(
            "Memorist routes are registered after the SPA catch-all mount "
            f"(memorist last index {report.memorist_last_index}, "
            f"spa index {report.spa_mount_index}); the proxy would be unreachable"
        )
    return report
