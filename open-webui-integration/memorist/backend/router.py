from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request

from ..shared.client import MemoristClient


@dataclass(frozen=True)
class OpenWebUIActor:
    user_uuid: str
    workspace_uuid: str


def require_openwebui_actor(request: Request) -> OpenWebUIActor:
    """Read identity installed by trusted Open WebUI authentication middleware.

    The mount integration must populate ``request.state.memorist_actor`` after validating
    the Open WebUI session and workspace membership. Headers, query values, local storage,
    and request JSON are intentionally ignored.
    """
    value = getattr(request.state, "memorist_actor", None)
    user_uuid = getattr(value, "user_uuid", None)
    workspace_uuid = getattr(value, "workspace_uuid", None)
    if not user_uuid or not workspace_uuid:
        raise HTTPException(status_code=401, detail="authenticated Open WebUI actor required")
    return OpenWebUIActor(str(user_uuid), str(workspace_uuid))


router = APIRouter(prefix="/api/v1/memorist", tags=["memorist-authenticated-proxy"])
AuthenticatedActor = Annotated[OpenWebUIActor, Depends(require_openwebui_actor)]


def _call(
    actor: OpenWebUIActor,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return MemoristClient().actor_request(
        method,
        f"/memcore{path}",
        user_id=actor.user_uuid,
        workspace_uuid=actor.workspace_uuid,
        payload=payload,
    )


@router.post("/memory-control/policy/resolve")
async def resolve_policy(request: Request, actor: AuthenticatedActor) -> dict[str, Any]:
    payload = await _object_body(request)
    payload.pop("user_uuid", None)
    payload.pop("workspace_uuid", None)
    payload.update({"user_uuid": actor.user_uuid, "workspace_uuid": actor.workspace_uuid})
    return _call(actor, "POST", "/memory-control/policy/resolve", payload)


@router.put("/memory-control/policy/defaults")
async def set_policy_default(request: Request, actor: AuthenticatedActor) -> dict[str, Any]:
    payload = await _object_body(request)
    payload.pop("workspace_uuid", None)
    if payload.get("scope_type") == "user":
        payload["scope_uuid"] = actor.user_uuid
    payload["workspace_uuid"] = actor.workspace_uuid
    return _call(actor, "PUT", "/memory-control/policy/defaults", payload)


@router.get("/memory-control/attachments/{attachment_uuid}/preview")
def preview_attachment(attachment_uuid: str, actor: AuthenticatedActor) -> dict[str, Any]:
    return _call(actor, "GET", f"/memory-control/attachments/{attachment_uuid}/preview")


@router.get("/memory-control/attachments/{attachment_uuid}/sources")
def attachment_sources(attachment_uuid: str, actor: AuthenticatedActor) -> dict[str, Any]:
    return _call(actor, "GET", f"/memory-control/attachments/{attachment_uuid}/sources")


def _attachment_action(action: str):
    async def endpoint(
        attachment_uuid: str,
        request: Request,
        actor: AuthenticatedActor,
    ) -> dict[str, Any]:
        return _call(
            actor,
            "POST",
            f"/memory-control/attachments/{attachment_uuid}/{action}",
            await _object_body(request),
        )

    return endpoint


for _action in (
    "approve",
    "suppress",
    "cancel",
    "delivery",
    "rejection",
    "regenerate-without-recall",
):
    router.add_api_route(
        f"/memory-control/attachments/{{attachment_uuid}}/{_action}",
        _attachment_action(_action),
        methods=["POST"],
    )


async def _object_body(request: Request) -> dict[str, Any]:
    value = await request.json()
    if not isinstance(value, dict):
        raise HTTPException(status_code=422, detail="JSON object required")
    return dict(value)
