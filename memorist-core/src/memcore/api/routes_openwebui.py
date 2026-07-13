from __future__ import annotations

import importlib
import json
from collections.abc import Iterator
from contextlib import contextmanager
from hashlib import sha256
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict

from memcore.config import Settings, get_settings
from memcore.memory_control import MemoryControlRepository, memory_control_connection
from memcore.memory_control.repository import ResolvedTurnPolicy
from memcore.models import new_uuid, utc_now
from memcore.openwebui.commands import (
    CaptureIdempotencyConflict,
    CaptureOpenWebUIMessageCommand,
)
from memcore.openwebui.session_resolution import (
    SessionResolutionInput,
    list_aliases,
)
from memcore.openwebui.session_resolution import (
    resolve_session as resolve_openwebui_session,
)
from memcore.reliability.consistency import run_consistency_check
from memcore.repositories import ProjectRepository, SessionRepository
from memcore.repositories.domain import WorkspaceRepository
from memcore.security import optional_memorist_actor
from memcore.storage.commands import FunctionWriteCommand
from memcore.storage.gateway import WriteGateway
from memcore.storage.postgres.compat import PostgresCompatConnection
from memcore.storage.sqlite import connect
from memcore.version import SCHEMA_VERSION, __version__

router = APIRouter(prefix="/memcore/openwebui", tags=["openwebui-integration"])


class SessionResolveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    openwebui_conversation_id: str | None = None
    temporary_chat_id: str | None = None
    client_session_nonce: str | None = None
    first_message_hash: str | None = None
    title: str | None = None
    user_id: str | None = None
    workspace_uuid: str | None = None
    source_app: str = "open_webui"
    created_at: str | None = None
    workspace_name: str = "Open WebUI"
    project_name: str = "Default"
    turn_policy: str | None = None
    attachment_review: bool | None = None


class MessageCaptureRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_uuid: str | None = None
    openwebui_conversation_id: str | None = None
    temporary_chat_id: str | None = None
    client_session_nonce: str | None = None
    first_message_hash: str | None = None
    openwebui_message_id: str | None = None
    source_message_id: str | None = None
    turn_index: int | None = None
    timestamp: str | None = None
    user_id: str | None = None
    source_app: str = "open_webui"
    role: str
    content: str
    idempotency_key: str | None = None
    raw_payload: dict[str, Any] | None = None
    turn_policy: str | None = None
    attachment_review: bool | None = None
    workspace_uuid: str | None = None


@router.post("/session/resolve", response_model=None)
def resolve_session(request: SessionResolveRequest) -> dict[str, Any]:
    settings = get_settings()
    actor = optional_memorist_actor()
    if actor is not None:
        if request.user_id not in {None, actor.user_uuid}:
            raise HTTPException(status_code=403, detail="user scope mismatch")
        if request.workspace_uuid not in {None, actor.workspace_uuid}:
            raise HTTPException(status_code=403, detail="workspace scope mismatch")
        request = request.model_copy(
            update={"user_id": actor.user_uuid, "workspace_uuid": actor.workspace_uuid}
        )
    if (
        not request.user_id or not request.workspace_uuid
    ) and not settings.allow_legacy_actor_headers_for_tests:
        return {
            "skipped": True,
            "reason": "trusted_actor_identity_unavailable",
            "session_uuid": None,
        }
    resolved_policy = _resolve_turn_policy(
        settings,
        user_uuid=request.user_id,
        chat_uuid=request.openwebui_conversation_id or request.temporary_chat_id,
        workspace_uuid=request.workspace_uuid,
        turn_override=request.turn_policy,
        attachment_review_override=request.attachment_review,
    )
    if resolved_policy.policy.private:
        return {"private": True, "turn_policy": "private", "session_uuid": None}
    if _is_full_postgres(settings):
        return _resolve_session_payload_full(settings, request)
    return _resolve_session_payload(request)


def _resolve_session_payload(request: SessionResolveRequest) -> dict[str, Any]:
    settings = get_settings()

    def handler(connection: Any) -> dict[str, Any]:
        workspace_uuid = (
            _ensure_workspace_uuid(connection, request.workspace_uuid, request.workspace_name)
            if request.workspace_uuid
            else _default_workspace_uuid(connection, request.workspace_name)
        )
        project_uuid = _default_project_uuid(connection, workspace_uuid, request.project_name)
        resolution = resolve_openwebui_session(
            connection,
            SessionResolutionInput(
                workspace_uuid=workspace_uuid,
                project_uuid=project_uuid,
                openwebui_conversation_id=request.openwebui_conversation_id,
                temporary_chat_id=request.temporary_chat_id,
                client_session_nonce=request.client_session_nonce,
                first_message_hash=request.first_message_hash,
                user_id=request.user_id,
                title=request.title,
                source_app=request.source_app,
                created_at=request.created_at,
            ),
        )
        session = resolution.session
        if request.user_id:
            _bind_session_actor(
                connection,
                session.session_uuid,
                request.user_id,
                workspace_uuid,
            )
            connection.commit()
        return {
            "session_uuid": session.session_uuid,
            "workspace_uuid": session.workspace_uuid,
            "project_uuid": session.project_uuid,
            "openwebui_conversation_id": session.openwebui_conversation_id,
            "matched_alias_type": resolution.matched_alias_type,
            "aliases": resolution.aliases,
            "diagnostics": resolution.diagnostics,
        }

    return WriteGateway(settings.db_path).submit(
        FunctionWriteCommand(
            command_type="openwebui_resolve_session",
            handler=handler,
            metadata={"target_type": "session"},
        ),
        timeout=2.0,
    )


@router.post("/messages/capture", response_model=None)
def capture_message(request: MessageCaptureRequest) -> dict[str, Any]:
    if request.role not in {"user", "assistant"}:
        raise HTTPException(status_code=400, detail="role must be user or assistant")
    settings = get_settings()
    actor = optional_memorist_actor()
    if actor is not None:
        if request.user_id not in {None, actor.user_uuid}:
            raise HTTPException(status_code=403, detail="user scope mismatch")
        if request.workspace_uuid not in {None, actor.workspace_uuid}:
            raise HTTPException(status_code=403, detail="workspace scope mismatch")
        request = request.model_copy(
            update={"user_id": actor.user_uuid, "workspace_uuid": actor.workspace_uuid}
        )
    if (
        not request.user_id or not request.workspace_uuid
    ) and not settings.allow_legacy_actor_headers_for_tests:
        return {
            "skipped": True,
            "reason": "trusted_actor_identity_unavailable",
            "session_uuid": None,
            "message_uuid": None,
            "duplicate": False,
        }
    resolved_policy = _resolve_turn_policy(
        settings,
        user_uuid=request.user_id,
        chat_uuid=request.openwebui_conversation_id or request.temporary_chat_id,
        workspace_uuid=request.workspace_uuid,
        turn_override=request.turn_policy,
        attachment_review_override=request.attachment_review,
    )
    if resolved_policy.policy.private:
        return {
            "skipped": True,
            "turn_policy": "private",
            "session_uuid": None,
            "message_uuid": None,
            "duplicate": False,
        }
    if _is_full_postgres(settings):
        result = _capture_message_full(
            settings,
            request,
            resolved_policy,
            enforce_session_actor=actor is not None,
        )
        return result

    session_uuid = request.session_uuid
    if session_uuid is None:
        resolved = _resolve_session_payload(
            SessionResolveRequest(
                openwebui_conversation_id=request.openwebui_conversation_id,
                temporary_chat_id=request.temporary_chat_id,
                client_session_nonce=request.client_session_nonce,
                first_message_hash=request.first_message_hash,
                user_id=request.user_id,
                workspace_uuid=request.workspace_uuid,
                source_app=request.source_app,
                created_at=request.timestamp,
            )
        )
        session_uuid = str(resolved["session_uuid"])
    else:
        with _connection() as connection:
            session = SessionRepository(connection).get_session(session_uuid)
            if session is None:
                raise HTTPException(status_code=404, detail="session not found")
            if request.workspace_uuid and session.workspace_uuid != request.workspace_uuid:
                raise HTTPException(status_code=403, detail="session workspace mismatch")
            if actor is not None and request.user_id and request.workspace_uuid:
                _assert_session_actor(
                    connection, session_uuid, request.user_id, request.workspace_uuid
                )

    idempotency_key = request.idempotency_key or _capture_key(request, session_uuid)
    try:
        result = WriteGateway(settings.db_path).submit(
            CaptureOpenWebUIMessageCommand(
                session_uuid=session_uuid,
                role=request.role,
                content=request.content,
                idempotency_key=idempotency_key,
                openwebui_conversation_id=request.openwebui_conversation_id,
                openwebui_message_id=request.openwebui_message_id,
                raw_payload=request.raw_payload,
                turn_index=request.turn_index,
            ),
            timeout=2.0,
        )
        response = {
            "session_uuid": result["session_uuid"],
            "message_uuid": result["message_uuid"],
            "duplicate": result["duplicate"],
        }
        _record_turn_contract(settings, request, response, resolved_policy)
        return response
    except CaptureIdempotencyConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.get("/sessions/{session_uuid}/lineage", response_model=None)
def session_lineage(session_uuid: str) -> dict[str, Any]:
    settings = get_settings()
    actor = optional_memorist_actor()
    if actor is None and not settings.allow_legacy_actor_headers_for_tests:
        raise HTTPException(status_code=401, detail="authenticated Memorist actor required")
    if _is_full_postgres(settings):
        with _pg_connection(settings) as connection:
            row = connection.execute(
                "SELECT * FROM sessions WHERE session_uuid = %s",
                (session_uuid,),
            ).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="session not found")
            if actor is not None and row.get("workspace_uuid") != actor.workspace_uuid:
                raise HTTPException(status_code=404, detail="session not found")
            return {
                "session_uuid": session_uuid,
                "openwebui_conversation_id": row.get("openwebui_conversation_id"),
                "aliases": _pg_aliases(connection, session_uuid),
            }
    with _connection() as connection:
        session = SessionRepository(connection).get_session(session_uuid)
        if session is None:
            raise HTTPException(status_code=404, detail="session not found")
        if actor is not None and session.workspace_uuid != actor.workspace_uuid:
            raise HTTPException(status_code=404, detail="session not found")
        return {
            "session_uuid": session_uuid,
            "openwebui_conversation_id": session.openwebui_conversation_id,
            "aliases": list_aliases(connection, session_uuid),
        }


@router.get("/status", response_model=None)
def integration_status() -> dict[str, Any]:
    settings = get_settings()
    actor = optional_memorist_actor()
    if actor is None and not settings.allow_legacy_actor_headers_for_tests:
        raise HTTPException(status_code=401, detail="authenticated Memorist actor required")
    if _is_full_postgres(settings):
        with _pg_connection(settings) as connection:
            last_attachment = connection.execute(
                """
                SELECT attachment_uuid, created_at, token_count
                FROM memory_context_attachments
                WHERE owner_user_uuid = %s AND workspace_uuid = %s
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (actor.user_uuid, actor.workspace_uuid) if actor is not None else (None, None),
            ).fetchone()
            return {
                "memorist_core": "connected",
                "version": __version__,
                "schema_version": SCHEMA_VERSION,
                "canonical_store": settings.canonical_store,
                "graph_backend": settings.graph_backend,
                "memory_mode": settings.retrieval_mode,
                "preflight": "enabled" if settings.preflight_enabled else "disabled",
                "last_attachment": _jsonable_dict(last_attachment) if last_attachment else None,
                "last_error": None,
            }
    with _connection() as connection:
        consistency = run_consistency_check(connection)
        last_attachment = connection.execute(
            """
            SELECT attachment_uuid, created_at, token_count
            FROM memory_context_attachments
            WHERE owner_user_uuid = ? AND workspace_uuid = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (actor.user_uuid, actor.workspace_uuid) if actor is not None else (None, None),
        ).fetchone()
        return {
            "memorist_core": "connected",
            "version": __version__,
            "schema_version": SCHEMA_VERSION,
            "sqlite": "ok" if consistency["status"] == "ok" else "warning",
            "graph_backend": settings.graph_backend,
            "memory_mode": settings.retrieval_mode,
            "preflight": "enabled" if settings.preflight_enabled else "disabled",
            "last_attachment": dict(last_attachment) if last_attachment else None,
            "last_error": None,
        }


@contextmanager
def _connection() -> Iterator[Any]:
    settings = get_settings()
    connection = connect(settings.db_path)
    try:
        yield connection
    finally:
        connection.close()


def _is_full_postgres(settings: Settings) -> bool:
    return settings.runtime_profile == "full" and settings.canonical_store == "postgres"


def _resolve_turn_policy(
    settings: Settings,
    *,
    user_uuid: str | None,
    chat_uuid: str | None,
    workspace_uuid: str | None,
    turn_override: str | None,
    attachment_review_override: bool | None,
) -> ResolvedTurnPolicy:
    with memory_control_connection(settings) as connection:
        return MemoryControlRepository(connection, settings.runtime_profile).resolve_policy(
            system_default=settings.default_turn_policy,
            workspace_uuid=workspace_uuid,
            user_uuid=user_uuid,
            chat_uuid=chat_uuid,
            turn_override=turn_override,
            attachment_review_override=attachment_review_override,
        )


def _record_turn_contract(
    settings: Settings,
    request: MessageCaptureRequest,
    result: dict[str, Any],
    resolved: ResolvedTurnPolicy,
) -> None:
    if request.role != "user" or bool(result.get("skipped")):
        return
    session_uuid = str(result["session_uuid"])
    with memory_control_connection(settings) as connection:
        session = connection.execute(
            "SELECT workspace_uuid FROM sessions WHERE session_uuid = ?", (session_uuid,)
        ).fetchone()
        workspace_uuid = request.workspace_uuid or (session["workspace_uuid"] if session else None)
        repository = MemoryControlRepository(connection, settings.runtime_profile)
        repository.record_turn_contract(
            input_message_uuid=str(result["message_uuid"]),
            session_uuid=session_uuid,
            workspace_uuid=workspace_uuid,
            user_uuid=request.user_id,
            chat_uuid=request.openwebui_conversation_id or request.temporary_chat_id,
            resolved=resolved,
        )
        repository.audit(
            "user_capture",
            resolved.policy,
            session_uuid=session_uuid,
            input_message_uuid=str(result["message_uuid"]),
            workspace_uuid=workspace_uuid,
            user_uuid=request.user_id,
            detail={"duplicate": bool(result.get("duplicate"))},
        )


@contextmanager
def _pg_connection(settings: Settings) -> Iterator[Any]:
    if not settings.postgres_dsn:
        raise RuntimeError("postgres_dsn is required for Full Mode OpenWebUI integration")
    psycopg = importlib.import_module("psycopg")
    rows = importlib.import_module("psycopg.rows")
    connection = psycopg.connect(settings.postgres_dsn, row_factory=rows.dict_row)
    try:
        yield connection
    finally:
        connection.close()


def _resolve_session_payload_full(
    settings: Settings,
    request: SessionResolveRequest,
) -> dict[str, Any]:
    conversation_id = _normalize(request.openwebui_conversation_id)
    user_id = _normalize(request.user_id)
    diagnostics: list[str] = []
    with _pg_connection(settings) as connection, connection.transaction():
        workspace_uuid = (
            _pg_ensure_workspace_uuid(connection, request.workspace_uuid, request.workspace_name)
            if request.workspace_uuid
            else _pg_default_workspace_uuid(connection, request.workspace_name)
        )
        project_uuid = _pg_default_project_uuid(connection, workspace_uuid, request.project_name)
        session = _pg_session_for_alias(connection, conversation_id, user_id, workspace_uuid)
        matched_alias_type = "openwebui_chat_id" if session is not None else None
        if session is None:
            session = _pg_legacy_session_by_conversation_id(
                connection, conversation_id, workspace_uuid
            )
            if session is not None:
                matched_alias_type = "legacy_openwebui_conversation_id"
        if session is None:
            now = utc_now()
            session = {
                "session_uuid": new_uuid(),
                "workspace_uuid": workspace_uuid,
                "project_uuid": project_uuid,
                "openwebui_conversation_id": conversation_id,
                "title": request.title,
            }
            connection.execute(
                """
                    INSERT INTO sessions (
                        session_uuid, workspace_uuid, project_uuid, openwebui_conversation_id,
                        title, status, created_at, updated_at, schema_version
                    )
                    VALUES (%s, %s, %s, %s, %s, 'active', %s, %s, 1)
                    """,
                (
                    session["session_uuid"],
                    workspace_uuid,
                    project_uuid,
                    conversation_id,
                    request.title,
                    now,
                    now,
                ),
            )
            diagnostics.append("created_session_without_existing_alias")
        if request.user_id:
            _pg_bind_session_actor(
                connection,
                str(session["session_uuid"]),
                request.user_id,
                workspace_uuid,
            )
        if conversation_id:
            _pg_attach_alias(connection, str(session["session_uuid"]), conversation_id, user_id)
        aliases = _pg_aliases(connection, str(session["session_uuid"]))
        return {
            "session_uuid": session["session_uuid"],
            "workspace_uuid": session.get("workspace_uuid"),
            "project_uuid": session.get("project_uuid"),
            "openwebui_conversation_id": session.get("openwebui_conversation_id"),
            "matched_alias_type": matched_alias_type,
            "aliases": aliases,
            "diagnostics": diagnostics,
        }


def _capture_message_full(
    settings: Settings,
    request: MessageCaptureRequest,
    resolved_policy: ResolvedTurnPolicy,
    *,
    enforce_session_actor: bool,
) -> dict[str, Any]:
    session_uuid = request.session_uuid
    if session_uuid is None:
        resolved = _resolve_session_payload_full(
            settings,
            SessionResolveRequest(
                openwebui_conversation_id=request.openwebui_conversation_id,
                temporary_chat_id=request.temporary_chat_id,
                client_session_nonce=request.client_session_nonce,
                first_message_hash=request.first_message_hash,
                user_id=request.user_id,
                workspace_uuid=request.workspace_uuid,
                source_app=request.source_app,
                created_at=request.timestamp,
            ),
        )
        session_uuid = str(resolved["session_uuid"])
    idempotency_key = request.idempotency_key or _capture_key(request, session_uuid)
    content_hash = sha256(request.content.encode("utf-8")).hexdigest()
    creator_type = "user" if request.role == "user" else "model"

    with _pg_connection(settings) as connection:  # noqa: SIM117
        with connection.transaction():
            session = _pg_session_exists(connection, session_uuid)
            if session is None:
                raise HTTPException(status_code=404, detail="session not found")
            if request.workspace_uuid and session.get("workspace_uuid") != request.workspace_uuid:
                raise HTTPException(status_code=403, detail="session workspace mismatch")
            if enforce_session_actor and request.user_id and request.workspace_uuid:
                _pg_assert_session_actor(
                    connection, session_uuid, request.user_id, request.workspace_uuid
                )
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (f"openwebui-capture:{session_uuid}",),
            )
            existing = connection.execute(
                """
                SELECT session_uuid, message_uuid, role, content_hash,
                       openwebui_message_id, openwebui_conversation_id
                FROM openwebui_message_captures
                WHERE idempotency_key = %s
                """,
                (idempotency_key,),
            ).fetchone()
            if existing is not None:
                fingerprint = (
                    str(existing["session_uuid"]),
                    str(existing["role"]),
                    str(existing["content_hash"]),
                    existing.get("openwebui_message_id"),
                    existing.get("openwebui_conversation_id"),
                )
                expected = (
                    session_uuid,
                    request.role,
                    content_hash,
                    request.openwebui_message_id,
                    request.openwebui_conversation_id,
                )
                if fingerprint != expected:
                    raise HTTPException(
                        status_code=409,
                        detail="capture idempotency key reused with a different request",
                    )
                result = {
                    "session_uuid": existing["session_uuid"],
                    "message_uuid": existing["message_uuid"],
                    "duplicate": True,
                }
                _record_turn_contract_on_postgres_connection(
                    connection, settings, request, result, resolved_policy
                )
                return result
            turn_index = request.turn_index
            if turn_index is None:
                row = connection.execute(
                    """
                    SELECT COALESCE(MAX(turn_index), -1) + 1 AS next_turn_index
                    FROM messages
                    WHERE session_uuid = %s
                    """,
                    (session_uuid,),
                ).fetchone()
                turn_index = int(row["next_turn_index"])
            message_uuid = new_uuid()
            now = utc_now()
            connection.execute(
                """
                INSERT INTO messages (
                    message_uuid, session_uuid, turn_index, role, creator_type, raw_text,
                    processing_status, visibility, is_deleted, created_at, updated_at,
                    schema_version
                )
                VALUES (%s, %s, %s, %s, %s, %s, 'pending', 'visible', false, %s, %s, 1)
                """,
                (
                    message_uuid,
                    session_uuid,
                    turn_index,
                    request.role,
                    creator_type,
                    request.content,
                    now,
                    now,
                ),
            )
            event_uuid = _pg_append_session_event(
                connection,
                session_uuid,
                "message_created",
                {
                    "message_uuid": message_uuid,
                    "role": request.role,
                    "creator_type": creator_type,
                    "source": "openwebui_full_adapter",
                },
                actor_type=creator_type,
                actor_uuid=None,
            )
            connection.execute(
                """
                INSERT INTO openwebui_message_captures (
                    capture_uuid, idempotency_key, openwebui_conversation_id,
                    openwebui_message_id, role, content_hash, session_uuid,
                    message_uuid, created_at, schema_version
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 1)
                """,
                (
                    new_uuid(),
                    idempotency_key,
                    request.openwebui_conversation_id,
                    request.openwebui_message_id,
                    request.role,
                    content_hash,
                    session_uuid,
                    message_uuid,
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO openwebui_capture_dedup (
                    dedup_key, session_uuid, message_uuid, event_uuid, created_at, schema_version
                )
                VALUES (%s, %s, %s, %s, %s, 1)
                ON CONFLICT (dedup_key) DO NOTHING
                """,
                (idempotency_key, session_uuid, message_uuid, event_uuid, now),
            )
            _pg_enqueue_capture_jobs(
                connection,
                session_uuid=session_uuid,
                message_uuid=message_uuid,
                now=now,
            )
            result = {
                "session_uuid": session_uuid,
                "message_uuid": message_uuid,
                "duplicate": False,
            }
            _record_turn_contract_on_postgres_connection(
                connection, settings, request, result, resolved_policy
            )
            return result


def _record_turn_contract_on_postgres_connection(
    connection: Any,
    settings: Settings,
    request: MessageCaptureRequest,
    result: dict[str, Any],
    resolved: ResolvedTurnPolicy,
) -> None:
    if request.role != "user" or bool(result.get("skipped")):
        return
    session_uuid = str(result["session_uuid"])
    session = connection.execute(
        "SELECT workspace_uuid FROM sessions WHERE session_uuid = %s", (session_uuid,)
    ).fetchone()
    workspace_uuid = request.workspace_uuid or (session["workspace_uuid"] if session else None)
    repository = MemoryControlRepository(
        PostgresCompatConnection(connection),
        settings.runtime_profile,
        autocommit=False,
    )
    repository.record_turn_contract(
        input_message_uuid=str(result["message_uuid"]),
        session_uuid=session_uuid,
        workspace_uuid=workspace_uuid,
        user_uuid=request.user_id,
        chat_uuid=request.openwebui_conversation_id or request.temporary_chat_id,
        resolved=resolved,
    )
    repository.audit(
        "user_capture",
        resolved.policy,
        session_uuid=session_uuid,
        input_message_uuid=str(result["message_uuid"]),
        workspace_uuid=workspace_uuid,
        user_uuid=request.user_id,
        detail={"duplicate": bool(result.get("duplicate"))},
    )


def _pg_enqueue_capture_jobs(
    connection: Any,
    *,
    session_uuid: str,
    message_uuid: str,
    now: str,
) -> None:
    profile = connection.execute(
        """
        SELECT p.model_profile_uuid, p.provider_type, p.model_name
        FROM model_role_defaults d
        JOIN model_profiles p ON p.model_profile_uuid = d.model_profile_uuid
        WHERE d.role = 'memory_extraction' AND COALESCE(p.is_enabled, true) = true
        ORDER BY CASE WHEN d.project_uuid IS NOT NULL THEN 0
                      WHEN d.workspace_uuid IS NOT NULL THEN 1 ELSE 2 END
        LIMIT 1
        """
    ).fetchone()
    model_identity = {
        "model_role": "memory_extraction",
        "model_profile_uuid": profile["model_profile_uuid"] if profile else None,
        "provider_type": profile["provider_type"] if profile else "deterministic",
        "model_name": profile["model_name"] if profile else "deterministic_extraction",
    }
    jobs = [("text_unitization", 100), ("memory_extraction", 60)]
    for job_type, priority in jobs:
        job_uuid = new_uuid()
        payload = json.dumps(
            {
                "message_uuid": message_uuid,
                "session_uuid": session_uuid,
                **(model_identity if job_type == "memory_extraction" else {}),
            },
            sort_keys=True,
        )
        connection.execute(
            """
            INSERT INTO jobs (
                job_uuid, job_type, priority, status, payload_jsonb, payload_ijson,
                idempotency_key, run_after, attempts, max_attempts, created_at, updated_at,
                schema_version
            )
            VALUES (%s, %s, %s, 'pending', %s::jsonb, %s, %s, %s, 0, 3, %s, %s, 1)
            ON CONFLICT (job_type, idempotency_key) DO NOTHING
            """,
            (
                job_uuid,
                job_type,
                priority,
                payload,
                payload,
                f"openwebui:{job_type}:{message_uuid}",
                now,
                now,
                now,
            ),
        )
        if job_type == "memory_extraction":
            connection.execute(
                """
                INSERT INTO model_usage_events (
                    usage_event_uuid, model_profile_uuid, role, event_type, stage,
                    provider_type, model_name, session_uuid, message_uuid, job_uuid,
                    input_tokens, output_tokens, embedding_count, status, created_at,
                    schema_version
                ) VALUES (%s, %s, 'memory_extraction', 'queued', 'memory_extraction_queued',
                          %s, %s, %s, %s, %s, 0, 0, 0, 'queued', %s, 1)
                ON CONFLICT (usage_event_uuid) DO NOTHING
                """,
                (
                    new_uuid(),
                    model_identity["model_profile_uuid"],
                    model_identity["provider_type"],
                    model_identity["model_name"],
                    session_uuid,
                    message_uuid,
                    job_uuid,
                    now,
                ),
            )


def _pg_default_workspace_uuid(connection: Any, name: str) -> str:
    row = connection.execute(
        "SELECT workspace_uuid FROM workspaces ORDER BY created_at, workspace_uuid LIMIT 1"
    ).fetchone()
    if row is not None:
        return str(row["workspace_uuid"])
    workspace_uuid = new_uuid()
    now = utc_now()
    connection.execute(
        """
        INSERT INTO workspaces (
            workspace_uuid, name, description, created_at, updated_at, schema_version
        )
        VALUES (%s, %s, NULL, %s, %s, 1)
        """,
        (workspace_uuid, name, now, now),
    )
    return workspace_uuid


def _pg_ensure_workspace_uuid(connection: Any, workspace_uuid: str, name: str) -> str:
    row = connection.execute(
        "SELECT workspace_uuid FROM workspaces WHERE workspace_uuid = %s",
        (workspace_uuid,),
    ).fetchone()
    if row is not None:
        return workspace_uuid
    now = utc_now()
    connection.execute(
        """
        INSERT INTO workspaces (
            workspace_uuid, name, description, created_at, updated_at, schema_version
        ) VALUES (%s, %s, NULL, %s, %s, 1)
        """,
        (workspace_uuid, name, now, now),
    )
    return workspace_uuid


def _pg_default_project_uuid(connection: Any, workspace_uuid: str, name: str) -> str:
    row = connection.execute(
        """
        SELECT project_uuid
        FROM projects
        WHERE workspace_uuid = %s
        ORDER BY created_at, project_uuid
        LIMIT 1
        """,
        (workspace_uuid,),
    ).fetchone()
    if row is not None:
        return str(row["project_uuid"])
    project_uuid = new_uuid()
    now = utc_now()
    connection.execute(
        """
        INSERT INTO projects (
            project_uuid, workspace_uuid, name, description, created_at, updated_at, schema_version
        )
        VALUES (%s, %s, %s, NULL, %s, %s, 1)
        """,
        (project_uuid, workspace_uuid, name, now, now),
    )
    return project_uuid


def _pg_session_for_alias(
    connection: Any,
    conversation_id: str | None,
    user_id: str | None,
    workspace_uuid: str,
) -> dict[str, Any] | None:
    if not conversation_id:
        return None
    row = connection.execute(
        """
        SELECT s.*
        FROM openwebui_session_aliases AS a
        JOIN sessions AS s ON s.session_uuid = a.session_uuid
        WHERE a.openwebui_chat_id = %s
          AND COALESCE(a.openwebui_user_id, '') = COALESCE(%s, '')
          AND s.workspace_uuid = %s
        ORDER BY a.created_at DESC
        LIMIT 1
        """,
        (conversation_id, user_id, workspace_uuid),
    ).fetchone()
    return dict(row) if row is not None else None


def _pg_legacy_session_by_conversation_id(
    connection: Any,
    conversation_id: str | None,
    workspace_uuid: str,
) -> dict[str, Any] | None:
    if not conversation_id:
        return None
    row = connection.execute(
        """
        SELECT *
        FROM sessions
        WHERE openwebui_conversation_id = %s
          AND workspace_uuid = %s
        ORDER BY created_at
        LIMIT 1
        """,
        (conversation_id, workspace_uuid),
    ).fetchone()
    return dict(row) if row is not None else None


def _pg_session_exists(connection: Any, session_uuid: str) -> dict[str, Any] | None:
    row = connection.execute(
        "SELECT * FROM sessions WHERE session_uuid = %s",
        (session_uuid,),
    ).fetchone()
    return dict(row) if row is not None else None


def _pg_bind_session_actor(
    connection: Any, session_uuid: str, user_uuid: str, workspace_uuid: str
) -> None:
    existing = connection.execute(
        "SELECT user_uuid, workspace_uuid FROM memorist_session_actors WHERE session_uuid = %s",
        (session_uuid,),
    ).fetchone()
    if existing is None:
        connection.execute(
            "INSERT INTO memorist_session_actors "
            "(session_uuid, user_uuid, workspace_uuid, created_at, schema_version) "
            "VALUES (%s, %s, %s, %s, 1)",
            (session_uuid, user_uuid, workspace_uuid, utc_now()),
        )
        return
    if existing["user_uuid"] != user_uuid or existing["workspace_uuid"] != workspace_uuid:
        raise HTTPException(status_code=404, detail="session not found")


def _pg_assert_session_actor(
    connection: Any, session_uuid: str, user_uuid: str, workspace_uuid: str
) -> None:
    row = connection.execute(
        "SELECT 1 FROM memorist_session_actors "
        "WHERE session_uuid = %s AND user_uuid = %s AND workspace_uuid = %s",
        (session_uuid, user_uuid, workspace_uuid),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="session not found")


def _pg_attach_alias(
    connection: Any,
    session_uuid: str,
    conversation_id: str,
    user_id: str | None,
) -> None:
    existing = connection.execute(
        """
        SELECT alias_uuid
        FROM openwebui_session_aliases
        WHERE openwebui_chat_id = %s
          AND COALESCE(openwebui_user_id, '') = COALESCE(%s, '')
        LIMIT 1
        """,
        (conversation_id, user_id),
    ).fetchone()
    if existing is not None:
        return
    connection.execute(
        """
        INSERT INTO openwebui_session_aliases (
            alias_uuid, session_uuid, openwebui_chat_id, openwebui_user_id,
            alias_type, created_at, schema_version
        )
        VALUES (%s, %s, %s, %s, 'openwebui_chat_id', %s, 1)
        ON CONFLICT DO NOTHING
        """,
        (new_uuid(), session_uuid, conversation_id, user_id, utc_now()),
    )


def _pg_aliases(connection: Any, session_uuid: str) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT alias_uuid, session_uuid, openwebui_chat_id, openwebui_user_id,
               alias_type, created_at, schema_version
        FROM openwebui_session_aliases
        WHERE session_uuid = %s
        ORDER BY created_at, alias_type
        """,
        (session_uuid,),
    ).fetchall()
    return [_jsonable_dict(row) for row in rows]


def _pg_append_session_event(
    connection: Any,
    session_uuid: str,
    event_type: str,
    payload: dict[str, Any],
    actor_type: str | None,
    actor_uuid: str | None,
) -> str:
    row = connection.execute(
        """
        SELECT COALESCE(MAX(event_index), -1) + 1 AS next_event_index
        FROM session_events
        WHERE session_uuid = %s
        """,
        (session_uuid,),
    ).fetchone()
    event_index = int(row["next_event_index"])
    event_uuid = new_uuid()
    payload_text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    content_hash = sha256(payload_text.encode("utf-8")).hexdigest()
    connection.execute(
        """
        INSERT INTO session_events (
            event_uuid, session_uuid, event_type, event_index, payload_jsonb,
            actor_type, actor_uuid, content_hash, created_at, schema_version
        )
        VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, 1)
        """,
        (
            event_uuid,
            session_uuid,
            event_type,
            event_index,
            payload_text,
            actor_type,
            actor_uuid,
            content_hash,
            utc_now(),
        ),
    )
    return event_uuid


def _default_workspace_uuid(connection: Any, name: str) -> str:
    repository = WorkspaceRepository(connection)
    workspaces = repository.list_workspaces()
    if workspaces:
        return workspaces[0].workspace_uuid
    return repository.create_workspace(name).workspace_uuid


def _ensure_workspace_uuid(connection: Any, workspace_uuid: str, name: str) -> str:
    row = connection.execute(
        "SELECT workspace_uuid FROM workspaces WHERE workspace_uuid = ?",
        (workspace_uuid,),
    ).fetchone()
    if row is not None:
        return workspace_uuid
    now = utc_now()
    connection.execute(
        """
        INSERT INTO workspaces (
            workspace_uuid, name, description, created_at, updated_at, schema_version
        ) VALUES (?, ?, NULL, ?, ?, 1)
        """,
        (workspace_uuid, name, now, now),
    )
    return workspace_uuid


def _bind_session_actor(
    connection: Any, session_uuid: str, user_uuid: str, workspace_uuid: str
) -> None:
    existing = connection.execute(
        "SELECT user_uuid, workspace_uuid FROM memorist_session_actors WHERE session_uuid = ?",
        (session_uuid,),
    ).fetchone()
    if existing is None:
        connection.execute(
            "INSERT INTO memorist_session_actors "
            "(session_uuid, user_uuid, workspace_uuid, created_at, schema_version) "
            "VALUES (?, ?, ?, ?, 1)",
            (session_uuid, user_uuid, workspace_uuid, utc_now()),
        )
        return
    if existing["user_uuid"] != user_uuid or existing["workspace_uuid"] != workspace_uuid:
        raise HTTPException(status_code=404, detail="session not found")


def _assert_session_actor(
    connection: Any, session_uuid: str, user_uuid: str, workspace_uuid: str
) -> None:
    row = connection.execute(
        "SELECT 1 FROM memorist_session_actors "
        "WHERE session_uuid = ? AND user_uuid = ? AND workspace_uuid = ?",
        (session_uuid, user_uuid, workspace_uuid),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="session not found")


def _default_project_uuid(connection: Any, workspace_uuid: str, name: str) -> str:
    repository = ProjectRepository(connection)
    projects = repository.list_projects(workspace_uuid)
    if projects:
        return projects[0].project_uuid
    return repository.create_project(workspace_uuid, name).project_uuid


def _find_session(connection: Any, openwebui_conversation_id: str | None) -> Any | None:
    if not openwebui_conversation_id:
        return None
    row = connection.execute(
        """
        SELECT *
        FROM sessions
        WHERE openwebui_conversation_id = ?
        ORDER BY created_at
        LIMIT 1
        """,
        (openwebui_conversation_id,),
    ).fetchone()
    if row is None:
        return None
    return SessionRepository(connection).get_session(row["session_uuid"])


def _capture_key(request: MessageCaptureRequest, session_uuid: str) -> str:
    source_message_id = request.source_message_id or request.openwebui_message_id or ""
    material = "|".join(
        [
            request.source_app,
            session_uuid,
            request.role,
            source_message_id,
            str(request.turn_index) if request.turn_index is not None else "",
            sha256(request.content.encode("utf-8")).hexdigest(),
            _timestamp_bucket(request.timestamp),
        ]
    )
    return sha256(material.encode("utf-8")).hexdigest()


def _timestamp_bucket(value: str | None) -> str:
    if not value:
        return ""
    return value[:16] if len(value) >= 16 else value


def _sanitize_error(error: str) -> str:
    lowered = error.lower()
    if any(part in lowered for part in ("key", "token", "secret", "password")):
        return "[redacted]"
    return error[:160]


def _normalize(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _jsonable_dict(row: Any) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in dict(row).items():
        if hasattr(value, "isoformat"):
            result[str(key)] = value.isoformat()
        else:
            result[str(key)] = value
    return result
