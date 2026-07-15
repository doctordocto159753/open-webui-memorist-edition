from __future__ import annotations

from hashlib import sha256
from typing import Any

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from memcore.attachments.display import build_attachment_display
from memcore.config import get_settings
from memcore.memory_control import MemoryControlRepository, memory_control_connection
from memcore.memory_control.policy import normalize_turn_policy, reject_runtime_override
from memcore.models import new_uuid, utc_now
from memcore.security import current_memorist_actor, optional_memorist_actor

router = APIRouter(prefix="/memcore/memory-control", tags=["memory-control"])


class MemoristRequestControl(BaseModel):
    model_config = ConfigDict(extra="forbid")

    turn_policy: str | None = None
    attachment_review: bool | None = None


class PolicyResolveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_uuid: str | None = None
    chat_uuid: str | None = None
    workspace_uuid: str | None = None
    memorist: MemoristRequestControl = Field(default_factory=MemoristRequestControl)


class PolicyDefaultRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope_type: str
    scope_uuid: str
    workspace_uuid: str | None = None
    turn_policy: str
    attachment_review: bool = False


class AttachmentActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotency_key: str = Field(min_length=8, max_length=200)
    response_message_uuid: str | None = None


def _actor(
    x_memorist_user_id: str | None,
    x_memorist_workspace_id: str | None,
) -> tuple[str, str | None]:
    actor = optional_memorist_actor()
    if actor is not None:
        if x_memorist_user_id and x_memorist_user_id != actor.user_uuid:
            raise HTTPException(status_code=403, detail="raw actor user mismatch")
        if x_memorist_workspace_id and x_memorist_workspace_id != actor.workspace_uuid:
            raise HTTPException(status_code=403, detail="raw actor workspace mismatch")
        return actor.user_uuid, actor.workspace_uuid
    settings = get_settings()
    if settings.allow_legacy_actor_headers_for_tests and x_memorist_user_id:
        return x_memorist_user_id, x_memorist_workspace_id
    authenticated = current_memorist_actor()
    return authenticated.user_uuid, authenticated.workspace_uuid


@router.post("/policy/resolve", response_model=None)
def resolve_policy(request: PolicyResolveRequest) -> dict[str, Any]:
    settings = get_settings()
    actor = optional_memorist_actor()
    if actor is not None:
        if request.user_uuid not in {None, actor.user_uuid}:
            raise HTTPException(status_code=403, detail="user scope mismatch")
        if request.workspace_uuid not in {None, actor.workspace_uuid}:
            raise HTTPException(status_code=403, detail="workspace scope mismatch")
        request = request.model_copy(
            update={
                "user_uuid": actor.user_uuid,
                "workspace_uuid": actor.workspace_uuid,
            }
        )
    reject_runtime_override(request.model_dump(mode="json").get("memorist"))
    with memory_control_connection(settings) as connection:
        resolved = MemoryControlRepository(
            connection, settings.runtime_profile
        ).resolve_policy(
            system_default=settings.default_turn_policy,
            workspace_uuid=request.workspace_uuid,
            user_uuid=request.user_uuid,
            chat_uuid=request.chat_uuid,
            turn_override=request.memorist.turn_policy,
            attachment_review_override=request.memorist.attachment_review,
        )
    return {
        "policy": resolved.policy.model_dump(mode="json"),
        "source": resolved.source,
        "attachment_review": resolved.attachment_review,
        "runtime_profile": settings.runtime_profile,
        "runtime_profile_server_controlled": True,
        "openwebui_native_memory_independent": True,
    }


@router.put("/policy/defaults", response_model=None)
def set_policy_default(
    request: PolicyDefaultRequest,
    x_memorist_user_id: str | None = Header(default=None),
    x_memorist_workspace_id: str | None = Header(default=None),
) -> dict[str, Any]:
    actor_user, actor_workspace = _actor(x_memorist_user_id, x_memorist_workspace_id)
    if request.scope_type == "user" and request.scope_uuid != actor_user:
        raise HTTPException(
            status_code=403, detail="cannot update another user's default"
        )
    if request.workspace_uuid != actor_workspace:
        raise HTTPException(status_code=403, detail="workspace scope mismatch")
    if request.scope_type == "system":
        raise HTTPException(
            status_code=403, detail="system default is server-controlled"
        )
    settings = get_settings()
    with memory_control_connection(settings) as connection:
        if request.scope_type == "chat":
            owned_chat = connection.execute(
                """
                SELECT 1
                FROM openwebui_session_aliases alias
                JOIN sessions session ON session.session_uuid = alias.session_uuid
                WHERE alias.openwebui_chat_id = ? AND alias.openwebui_user_id = ?
                  AND COALESCE(session.workspace_uuid, '') = COALESCE(?, '')
                LIMIT 1
                """,
                (request.scope_uuid, actor_user, actor_workspace),
            ).fetchone()
            if owned_chat is None:
                raise HTTPException(
                    status_code=403, detail="chat scope is not owned by actor"
                )
        try:
            return MemoryControlRepository(
                connection, settings.runtime_profile
            ).set_default(
                scope_type=request.scope_type,
                scope_uuid=request.scope_uuid,
                workspace_uuid=request.workspace_uuid,
                turn_policy=request.turn_policy,
                attachment_review=request.attachment_review,
            )
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error


@router.get("/attachments/{attachment_uuid}/preview", response_model=None)
def preview_attachment(
    attachment_uuid: str,
    x_memorist_user_id: str | None = Header(default=None),
    x_memorist_workspace_id: str | None = Header(default=None),
) -> dict[str, Any]:
    actor_user, actor_workspace = _actor(x_memorist_user_id, x_memorist_workspace_id)
    settings = get_settings()
    with memory_control_connection(settings) as connection:
        attachment = MemoryControlRepository(
            connection, settings.runtime_profile
        ).attachment_for_actor(
            attachment_uuid, user_uuid=actor_user, workspace_uuid=actor_workspace
        )
        if attachment is None:
            raise HTTPException(status_code=404, detail="attachment not found")
        return _public_attachment(attachment)


@router.get("/attachments/{attachment_uuid}/display", response_model=None)
def display_attachment(
    attachment_uuid: str,
    x_memorist_user_id: str | None = Header(default=None),
    x_memorist_workspace_id: str | None = Header(default=None),
) -> dict[str, Any]:
    actor_user, actor_workspace = _actor(x_memorist_user_id, x_memorist_workspace_id)
    settings = get_settings()
    with memory_control_connection(settings) as connection:
        attachment = MemoryControlRepository(
            connection, settings.runtime_profile
        ).attachment_for_actor(
            attachment_uuid, user_uuid=actor_user, workspace_uuid=actor_workspace
        )
        if attachment is None:
            raise HTTPException(status_code=404, detail="attachment not found")
        return _display_attachment(connection, attachment)


@router.get("/attachments/{attachment_uuid}/sources", response_model=None)
def fetch_attachment_sources(
    attachment_uuid: str,
    x_memorist_user_id: str | None = Header(default=None),
    x_memorist_workspace_id: str | None = Header(default=None),
) -> dict[str, Any]:
    actor_user, actor_workspace = _actor(x_memorist_user_id, x_memorist_workspace_id)
    settings = get_settings()
    with memory_control_connection(settings) as connection:
        repository = MemoryControlRepository(connection, settings.runtime_profile)
        attachment = repository.attachment_for_actor(
            attachment_uuid, user_uuid=actor_user, workspace_uuid=actor_workspace
        )
        if attachment is None:
            raise HTTPException(status_code=404, detail="attachment not found")
        rows = connection.execute(
            """
            SELECT source_type, source_uuid, memory_uuid, memory_version_uuid,
                   workspace_uuid, provenance_ijson
            FROM memorist_retrieval_sources
            WHERE attachment_uuid = ? AND COALESCE(workspace_uuid, '') = COALESCE(?, '')
            ORDER BY created_at, retrieval_source_uuid
            """,
            (attachment_uuid, actor_workspace),
        ).fetchall()
        return {
            "attachment_uuid": attachment_uuid,
            "sources": [dict(row) for row in rows],
        }


def _transition(
    attachment_uuid: str,
    status: str,
    request: AttachmentActionRequest,
    user_id: str | None,
    workspace_id: str | None,
) -> dict[str, Any]:
    actor_user, actor_workspace = _actor(user_id, workspace_id)
    settings = get_settings()
    with memory_control_connection(settings) as connection:
        try:
            return MemoryControlRepository(
                connection, settings.runtime_profile
            ).transition_attachment(
                attachment_uuid,
                status,
                user_uuid=actor_user,
                workspace_uuid=actor_workspace,
                idempotency_key=request.idempotency_key,
                response_message_uuid=request.response_message_uuid,
            )
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error


@router.post("/attachments/{attachment_uuid}/approve", response_model=None)
def approve_attachment(
    attachment_uuid: str,
    request: AttachmentActionRequest,
    x_memorist_user_id: str | None = Header(default=None),
    x_memorist_workspace_id: str | None = Header(default=None),
) -> dict[str, Any]:
    return _transition(
        attachment_uuid,
        "approved",
        request,
        x_memorist_user_id,
        x_memorist_workspace_id,
    )


@router.post("/attachments/{attachment_uuid}/suppress", response_model=None)
def suppress_attachment(
    attachment_uuid: str,
    request: AttachmentActionRequest,
    x_memorist_user_id: str | None = Header(default=None),
    x_memorist_workspace_id: str | None = Header(default=None),
) -> dict[str, Any]:
    return _transition(
        attachment_uuid,
        "suppressed",
        request,
        x_memorist_user_id,
        x_memorist_workspace_id,
    )


@router.post("/attachments/{attachment_uuid}/cancel", response_model=None)
def cancel_attachment(
    attachment_uuid: str,
    request: AttachmentActionRequest,
    x_memorist_user_id: str | None = Header(default=None),
    x_memorist_workspace_id: str | None = Header(default=None),
) -> dict[str, Any]:
    return _transition(
        attachment_uuid,
        "cancelled_before_send",
        request,
        x_memorist_user_id,
        x_memorist_workspace_id,
    )


@router.post("/attachments/{attachment_uuid}/delivery", response_model=None)
def record_attachment_delivery(
    attachment_uuid: str,
    request: AttachmentActionRequest,
    x_memorist_user_id: str | None = Header(default=None),
    x_memorist_workspace_id: str | None = Header(default=None),
) -> dict[str, Any]:
    return _transition(
        attachment_uuid,
        "delivered",
        request,
        x_memorist_user_id,
        x_memorist_workspace_id,
    )


@router.post("/attachments/{attachment_uuid}/rejection", response_model=None)
def record_attachment_rejection(
    attachment_uuid: str,
    request: AttachmentActionRequest,
    x_memorist_user_id: str | None = Header(default=None),
    x_memorist_workspace_id: str | None = Header(default=None),
) -> dict[str, Any]:
    return _transition(
        attachment_uuid,
        "user_rejected",
        request,
        x_memorist_user_id,
        x_memorist_workspace_id,
    )


@router.post(
    "/attachments/{attachment_uuid}/regenerate-without-recall", response_model=None
)
def regenerate_without_recall(
    attachment_uuid: str,
    request: AttachmentActionRequest,
    x_memorist_user_id: str | None = Header(default=None),
    x_memorist_workspace_id: str | None = Header(default=None),
) -> dict[str, Any]:
    actor_user, actor_workspace = _actor(x_memorist_user_id, x_memorist_workspace_id)
    settings = get_settings()
    with memory_control_connection(settings) as connection:
        repository = MemoryControlRepository(connection, settings.runtime_profile)
        attachment = repository.attachment_for_actor(
            attachment_uuid, user_uuid=actor_user, workspace_uuid=actor_workspace
        )
        if attachment is None:
            raise HTTPException(status_code=404, detail="attachment not found")
        delivery_state = str(attachment.get("lifecycle_status") or "prepared")
        if delivery_state not in {"delivered", "used_for_response"}:
            raise HTTPException(
                status_code=409, detail="attachment was not used in a response"
            )
        if (
            attachment.get("stale_reason")
            or str(attachment.get("status") or "active") != "active"
        ):
            raise HTTPException(
                status_code=409, detail="stale attachment cannot be regenerated"
            )
        if _attachment_expired(attachment.get("expires_at")):
            raise HTTPException(
                status_code=409, detail="expired attachment cannot be regenerated"
            )
        input_message_uuid = str(attachment["input_message_uuid"])
        message = connection.execute(
            "SELECT raw_text FROM messages WHERE message_uuid = ?",
            (input_message_uuid,),
        ).fetchone()
        if message is None:
            raise HTTPException(
                status_code=409, detail="original input message is unavailable"
            )
        if not request.response_message_uuid:
            raise HTTPException(
                status_code=400, detail="source response_message_uuid is required"
            )
        response_link = connection.execute(
            """
            SELECT link.* FROM assistant_response_links link
            JOIN messages response ON response.message_uuid = link.assistant_message_uuid
            WHERE link.input_message_uuid = ? AND link.attachment_uuid = ?
              AND link.assistant_message_uuid = ? AND response.role = 'assistant'
            """,
            (input_message_uuid, attachment_uuid, request.response_message_uuid),
        ).fetchone()
        if response_link is None:
            raise HTTPException(
                status_code=409, detail="delivered response link is unavailable"
            )
        existing = connection.execute(
            "SELECT * FROM memorist_regenerations WHERE idempotency_key = ?",
            (request.idempotency_key,),
        ).fetchone()
        if existing is None:
            regeneration_uuid = new_uuid()
            connection.execute(
                """
                INSERT INTO memorist_regenerations (
                    regeneration_uuid, original_input_message_uuid, original_attachment_uuid,
                    source_response_link_uuid, source_assistant_message_uuid,
                    user_uuid, workspace_uuid, turn_policy, idempotency_key, status,
                    created_at, schema_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'no_recall', ?, 'requested', ?, 1)
                """,
                (
                    regeneration_uuid,
                    input_message_uuid,
                    attachment_uuid,
                    response_link["response_link_uuid"],
                    response_link["assistant_message_uuid"],
                    actor_user,
                    actor_workspace,
                    request.idempotency_key,
                    utc_now(),
                ),
            )
            connection.commit()
        else:
            if (
                str(existing["original_attachment_uuid"]) != attachment_uuid
                or str(existing["original_input_message_uuid"]) != input_message_uuid
                or existing["user_uuid"] != actor_user
                or existing["workspace_uuid"] != actor_workspace
            ):
                raise HTTPException(
                    status_code=409,
                    detail="idempotency key belongs to a different regeneration request",
                )
            regeneration_uuid = str(existing["regeneration_uuid"])
        if existing is None:
            repository.audit(
                "regeneration_requested",
                normalize_turn_policy("no_recall"),
                session_uuid=str(attachment["session_uuid"]),
                input_message_uuid=input_message_uuid,
                workspace_uuid=actor_workspace,
                user_uuid=actor_user,
                attachment_uuid=attachment_uuid,
            )
        return {
            "regeneration_uuid": regeneration_uuid,
            "original_input_message_uuid": input_message_uuid,
            "source_response_message_uuid": response_link["assistant_message_uuid"],
            "original_user_prompt": message["raw_text"],
            "turn_policy": "no_recall",
            "create_attachment": False,
            "duplicate_user_capture": False,
            "original_attachment_audit_preserved": True,
            "prompt_hash": sha256(str(message["raw_text"] or "").encode()).hexdigest(),
        }


def _public_attachment(attachment: dict[str, Any]) -> dict[str, Any]:
    return {
        "attachment_uuid": attachment["attachment_uuid"],
        "session_uuid": attachment["session_uuid"],
        "input_message_uuid": attachment["input_message_uuid"],
        "retrieval_run_uuid": attachment.get("retrieval_run_uuid"),
        "rendered_attachment": attachment.get("rendered_attachment"),
        "token_count": attachment.get("token_count", 0),
        "lifecycle_status": attachment.get("lifecycle_status", "prepared"),
        "delivery_state": attachment.get("lifecycle_status", "prepared"),
        "user_disposition": attachment.get("user_disposition", "none"),
        "attachment_review": bool(attachment.get("attachment_review", False)),
        "generation": attachment.get("generation", 1),
        "created_at": attachment.get("created_at"),
    }


def _display_attachment(connection: Any, attachment: dict[str, Any]) -> dict[str, Any]:
    return {
        **build_attachment_display(connection, attachment),
        "attachment_uuid": attachment["attachment_uuid"],
        "session_uuid": attachment["session_uuid"],
        "input_message_uuid": attachment["input_message_uuid"],
        "retrieval_run_uuid": attachment.get("retrieval_run_uuid"),
        "token_count": attachment.get("token_count", 0),
        "lifecycle_status": attachment.get("lifecycle_status", "prepared"),
        "delivery_state": attachment.get("lifecycle_status", "prepared"),
        "user_disposition": attachment.get("user_disposition", "none"),
        "attachment_review": bool(attachment.get("attachment_review", False)),
        "generation": attachment.get("generation", 1),
        "created_at": attachment.get("created_at"),
    }


def _attachment_expired(value: Any) -> bool:
    if value is None:
        return False
    from datetime import UTC, datetime

    parsed = (
        value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    )
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed <= datetime.now(UTC)
