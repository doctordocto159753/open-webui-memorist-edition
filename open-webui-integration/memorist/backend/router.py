from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request

from ..shared.client import MemoristClient


@dataclass(frozen=True)
class OpenWebUIActor:
    user_uuid: str
    workspace_uuid: str
    is_admin: bool = False


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
    return OpenWebUIActor(
        str(user_uuid),
        str(workspace_uuid),
        bool(getattr(value, "is_admin", False)),
    )


router = APIRouter(prefix="/api/v1/memorist", tags=["memorist-authenticated-proxy"])
AuthenticatedActor = Annotated[OpenWebUIActor, Depends(require_openwebui_actor)]


def require_openwebui_admin(actor: AuthenticatedActor) -> OpenWebUIActor:
    if not actor.is_admin:
        raise HTTPException(status_code=403, detail="Open WebUI admin access required")
    return actor


AuthenticatedAdmin = Annotated[OpenWebUIActor, Depends(require_openwebui_admin)]


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


@router.get("/model-control/setup/status")
def model_setup_status(actor: AuthenticatedAdmin) -> dict[str, Any]:
    workspace = quote(actor.workspace_uuid, safe="")
    return _call(actor, "GET", f"/model-control/setup/status?workspace_uuid={workspace}")


@router.get("/model-control/roles")
def model_control_roles(actor: AuthenticatedAdmin) -> dict[str, Any]:
    return _call(actor, "GET", "/model-control/roles")


@router.get("/model-control/profiles")
def model_control_profiles(actor: AuthenticatedAdmin) -> dict[str, Any]:
    return _call(actor, "GET", "/model-control/profiles")


@router.post("/model-control/profiles")
async def create_model_control_profile(
    request: Request,
    actor: AuthenticatedAdmin,
) -> dict[str, Any]:
    return _call(actor, "POST", "/model-control/profiles", await _object_body(request))


@router.patch("/model-control/profiles/{model_profile_uuid}")
async def patch_model_control_profile(
    model_profile_uuid: str,
    request: Request,
    actor: AuthenticatedAdmin,
) -> dict[str, Any]:
    return _call(
        actor,
        "PATCH",
        f"/model-control/profiles/{quote(model_profile_uuid, safe='')}",
        await _object_body(request),
    )


@router.post("/model-control/profiles/{model_profile_uuid}/test")
async def test_model_control_profile(
    model_profile_uuid: str,
    request: Request,
    actor: AuthenticatedAdmin,
) -> dict[str, Any]:
    return _call(
        actor,
        "POST",
        f"/model-control/profiles/{quote(model_profile_uuid, safe='')}/test",
        await _object_body(request),
    )


@router.get("/model-control/defaults")
def model_control_defaults(actor: AuthenticatedAdmin) -> dict[str, Any]:
    return _call(actor, "GET", "/model-control/defaults")


@router.post("/model-control/defaults")
async def set_model_control_default(
    request: Request,
    actor: AuthenticatedAdmin,
) -> dict[str, Any]:
    payload = await _object_body(request)
    payload.pop("workspace_uuid", None)
    payload.pop("project_uuid", None)
    payload["workspace_uuid"] = actor.workspace_uuid
    return _call(actor, "POST", "/model-control/defaults", payload)


@router.get("/model-control/health")
def model_control_health(actor: AuthenticatedAdmin) -> dict[str, Any]:
    return _call(actor, "GET", "/model-control/health")


@router.get("/model-control/privacy")
def model_control_privacy(actor: AuthenticatedAdmin) -> dict[str, Any]:
    return _call(actor, "GET", "/model-control/privacy")


@router.post("/model-control/privacy/acknowledge")
async def acknowledge_model_control_privacy(
    request: Request,
    actor: AuthenticatedAdmin,
) -> dict[str, Any]:
    return _call(
        actor,
        "POST",
        "/model-control/privacy/acknowledge",
        await _object_body(request),
    )


@router.post("/memory-control/policy/resolve")
async def resolve_policy(request: Request, actor: AuthenticatedActor) -> dict[str, Any]:
    payload = await _object_body(request)
    payload.pop("user_uuid", None)
    payload.pop("workspace_uuid", None)
    payload.update({"user_uuid": actor.user_uuid, "workspace_uuid": actor.workspace_uuid})
    return _call(actor, "POST", "/memory-control/policy/resolve", payload)


@router.get("/openwebui/status")
def openwebui_status(actor: AuthenticatedActor) -> dict[str, Any]:
    return MemoristClient().status(
        user_id=actor.user_uuid,
        workspace_uuid=actor.workspace_uuid,
    )


@router.post("/memory-control/review/prepare")
async def prepare_attachment_review(request: Request, actor: AuthenticatedActor) -> dict[str, Any]:
    payload = await _object_body(request)
    content = str(payload.get("content") or "").strip()
    message_id = str(payload.get("message_id") or "").strip()
    conversation_id = _optional_text(payload.get("conversation_id"))
    temporary_chat_id = _optional_text(payload.get("temporary_chat_id"))
    if not content or not message_id or not (conversation_id or temporary_chat_id):
        raise HTTPException(
            status_code=422,
            detail="content, message_id, and a conversation identifier are required",
        )
    client = MemoristClient()
    requested_turn_policy = _optional_text(payload.get("turn_policy"))
    request_control: dict[str, Any] = {"attachment_review": True}
    if requested_turn_policy is not None:
        request_control["turn_policy"] = requested_turn_policy
    policy = client.resolve_turn_policy(
        user_id=actor.user_uuid,
        chat_id=conversation_id or temporary_chat_id,
        workspace_id=actor.workspace_uuid,
        request_control=request_control,
    )
    if bool(getattr(policy, "private", False)):
        return {"status": "private", "turn_policy": policy.mode}
    if not policy.recall_enabled or not policy.attachment_enabled:
        return {"status": "disabled", "turn_policy": policy.mode}
    session = client.resolve_session(
        conversation_id,
        _optional_text(payload.get("title")),
        actor.user_uuid,
        actor.workspace_uuid,
        temporary_chat_id=temporary_chat_id,
        client_session_nonce=_optional_text(payload.get("client_session_nonce")),
        first_message_hash=_optional_text(payload.get("first_message_hash")),
        created_at=_optional_text(payload.get("timestamp")),
        turn_policy=policy.mode,
        attachment_review=True,
    )
    captured = client.capture_message(
        session.session_uuid,
        "user",
        content,
        openwebui_conversation_id=conversation_id,
        temporary_chat_id=temporary_chat_id,
        openwebui_message_id=message_id,
        source_message_id=message_id,
        timestamp=_optional_text(payload.get("timestamp")),
        user_id=actor.user_uuid,
        idempotency_key=f"review-prepare:{actor.user_uuid}:{message_id}",
        turn_policy=policy.mode,
        attachment_review=True,
        workspace_uuid=actor.workspace_uuid,
    )
    prepared = client.preflight(
        session.session_uuid,
        captured.message_uuid,
        target_model=_optional_text(payload.get("target_model")),
        model_provider=_optional_text(payload.get("model_provider")),
        model_context_window=_optional_int(payload.get("model_context_window")),
        recent_conversation_text=_optional_text(payload.get("recent_conversation_text")),
        turn_policy=policy.mode,
        user_id=actor.user_uuid,
        workspace_uuid=actor.workspace_uuid,
        attachment_review=True,
    )
    return {
        "status": prepared.status,
        "attachment_uuid": prepared.attachment_uuid,
        "input_message_uuid": captured.message_uuid,
        "session_uuid": session.session_uuid,
        "workspace_uuid": actor.workspace_uuid,
        "message_id": message_id,
        "generation": 1,
    }


@router.put("/memory-control/policy/defaults")
async def set_policy_default(request: Request, actor: AuthenticatedActor) -> dict[str, Any]:
    payload = await _object_body(request)
    payload.pop("workspace_uuid", None)
    if payload.get("scope_type") == "user":
        payload["scope_uuid"] = actor.user_uuid
    payload["workspace_uuid"] = actor.workspace_uuid
    return _call(actor, "PUT", "/memory-control/policy/defaults", payload)


@router.get("/memory-control/workflow/chats/{chat_uuid}")
def get_memory_workflow(chat_uuid: str, actor: AuthenticatedActor) -> dict[str, Any]:
    return _call(
        actor,
        "GET",
        f"/memory-control/workflow/chats/{quote(chat_uuid, safe='')}",
    )


@router.patch("/memory-control/workflow/chats/{chat_uuid}")
async def update_memory_workflow(
    chat_uuid: str,
    request: Request,
    actor: AuthenticatedActor,
) -> dict[str, Any]:
    payload = await _object_body(request)
    if set(payload) != {"memory_enabled"} or not isinstance(payload.get("memory_enabled"), bool):
        raise HTTPException(status_code=422, detail="memory_enabled boolean required")
    return _call(
        actor,
        "PATCH",
        f"/memory-control/workflow/chats/{quote(chat_uuid, safe='')}",
        {"memory_enabled": payload["memory_enabled"]},
    )


@router.get("/memory-control/attachments/{attachment_uuid}/preview")
def preview_attachment(attachment_uuid: str, actor: AuthenticatedActor) -> dict[str, Any]:
    return _call(actor, "GET", f"/memory-control/attachments/{attachment_uuid}/preview")


@router.get("/memory-control/attachments/{attachment_uuid}/display")
def display_attachment(attachment_uuid: str, actor: AuthenticatedActor) -> dict[str, Any]:
    return _call(actor, "GET", f"/memory-control/attachments/{attachment_uuid}/display")


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


def _optional_text(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _optional_int(value: Any) -> int | None:
    return int(value) if value is not None else None
