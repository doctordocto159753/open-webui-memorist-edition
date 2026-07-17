"""Automatic, idempotent provisioning of the Memorist chat filter.

The one-click product must not require anyone to copy or paste a Filter into
Open WebUI by hand. At every startup of the Memorist Open WebUI application we
upsert a managed global filter row into Open WebUI's own Functions table. The
row's content is a thin wrapper that imports the packaged filter from the
Memorist integration on ``PYTHONPATH``, so the executable logic stays in the
reviewed package instead of an opaque database blob, and upgrading the package
upgrades the behavior.

Idempotency: the wrapper carries an explicit content version. Startup only
writes when the stored row is missing, carries a different managed version, or
lost its ``is_active``/``is_global``/``type`` invariants. Re-running never
creates duplicates and never touches filters Memorist does not manage.

Failure policy: provisioning failures raise, which fails Open WebUI startup
loudly. A product whose chat pipeline silently lacks memory capture must not
present itself as healthy.
"""

from __future__ import annotations

import os
from typing import Any

MEMORIST_FILTER_ID = "memorist_memory_filter"
MEMORIST_FILTER_NAME = "Memorist Memory"
MEMORIST_FILTER_CONTENT_VERSION = "pr5g-1"

_WRAPPER_TEMPLATE = '''"""Memorist managed chat filter (auto-provisioned; do not edit).

managed-by: memorist
memorist-filter-content-version: {version}

This wrapper is upserted at every startup by
``memorist.backend.filter_provisioning``. The real implementation lives in the
reviewed ``memorist`` package on the backend PYTHONPATH; workspace identity is
injected server-side from MEMORIST_OPENWEBUI_WORKSPACE_UUID and is never read
from browser-controlled fields.
"""

import os

from memorist.filter.memorist_memory_filter import Filter as _MemoristPackagedFilter


def _server_side_user(__user__):
    user = dict(__user__ or {{}})
    workspace = os.environ.get("MEMORIST_OPENWEBUI_WORKSPACE_UUID")
    if workspace and not user.get("workspace_id"):
        user["workspace_id"] = workspace
    return user


class Filter(_MemoristPackagedFilter):
    def inlet(self, body, __user__=None):
        return super().inlet(body, __user__=_server_side_user(__user__))

    def outlet(self, body, __user__=None):
        return super().outlet(body, __user__=_server_side_user(__user__))
'''


class MemoristFilterProvisioningError(RuntimeError):
    """Startup-fatal failure to guarantee the managed Memorist chat filter."""


def managed_filter_content(version: str = MEMORIST_FILTER_CONTENT_VERSION) -> str:
    return _WRAPPER_TEMPLATE.format(version=version)


def _is_managed_current(function: Any) -> bool:
    marker = f"memorist-filter-content-version: {MEMORIST_FILTER_CONTENT_VERSION}"
    return (
        function is not None
        and getattr(function, "type", None) == "filter"
        and bool(getattr(function, "is_active", False))
        and bool(getattr(function, "is_global", False))
        and marker in str(getattr(function, "content", ""))
    )


async def ensure_memorist_filter_provisioned() -> dict[str, Any]:
    """Upsert the managed global Memorist filter row. Raises on any failure."""
    if not os.environ.get("MEMORIST_OPENWEBUI_WORKSPACE_UUID"):
        raise MemoristFilterProvisioningError(
            "MEMORIST_OPENWEBUI_WORKSPACE_UUID must be configured before the "
            "Memorist chat filter can be provisioned"
        )
    try:
        from open_webui.models.functions import FunctionForm, FunctionMeta, Functions
        from open_webui.models.users import Users
    except ImportError as error:  # pragma: no cover - only outside the host app
        raise MemoristFilterProvisioningError(
            "the pinned Open WebUI Functions model API is unavailable; refusing "
            "to start without the managed Memorist chat filter"
        ) from error

    existing = await Functions.get_function_by_id(MEMORIST_FILTER_ID)
    if _is_managed_current(existing):
        return {"filter_id": MEMORIST_FILTER_ID, "action": "unchanged"}

    content = managed_filter_content()
    meta = FunctionMeta(
        description=(
            "Managed by Memorist: captures chat turns, runs memory preflight "
            "retrieval, and records assistant completions. Auto-provisioned at "
            "startup; manual edits are overwritten."
        ),
        manifest={"managed_by": "memorist", "version": MEMORIST_FILTER_CONTENT_VERSION},
    )
    if existing is None:
        owner = await Users.get_super_admin_user()
        created = await Functions.insert_new_function(
            getattr(owner, "id", None) or "memorist-system",
            "filter",
            FunctionForm(
                id=MEMORIST_FILTER_ID,
                name=MEMORIST_FILTER_NAME,
                content=content,
                meta=meta,
            ),
        )
        if created is None:
            raise MemoristFilterProvisioningError(
                "Open WebUI rejected creation of the managed Memorist chat filter"
            )
        action = "created"
    else:
        updated = await Functions.update_function_by_id(
            MEMORIST_FILTER_ID,
            {
                "type": "filter",
                "name": MEMORIST_FILTER_NAME,
                "content": content,
                "meta": meta.model_dump(),
            },
        )
        if updated is None:
            raise MemoristFilterProvisioningError(
                "Open WebUI rejected the update of the managed Memorist chat filter"
            )
        action = "updated"

    activated = await Functions.update_function_by_id(
        MEMORIST_FILTER_ID, {"is_active": True, "is_global": True}
    )
    if not (
        activated is not None
        and getattr(activated, "is_active", False)
        and getattr(activated, "is_global", False)
    ):
        raise MemoristFilterProvisioningError(
            "the managed Memorist chat filter could not be activated globally"
        )
    verification = await Functions.get_function_by_id(MEMORIST_FILTER_ID)
    if not _is_managed_current(verification):
        raise MemoristFilterProvisioningError(
            "post-provisioning verification of the managed Memorist chat filter failed"
        )
    return {"filter_id": MEMORIST_FILTER_ID, "action": action}
