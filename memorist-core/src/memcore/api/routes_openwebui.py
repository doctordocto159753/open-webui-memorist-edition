from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from hashlib import sha256
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from memcore.config import get_settings
from memcore.openwebui.commands import CaptureOpenWebUIMessageCommand
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
from memcore.storage.commands import FunctionWriteCommand
from memcore.storage.gateway import WriteGateway
from memcore.storage.migrations import apply_migrations
from memcore.storage.sqlite import connect
from memcore.version import SCHEMA_VERSION, __version__

router = APIRouter(prefix="/memcore/openwebui", tags=["openwebui-integration"])


class SessionResolveRequest(BaseModel):
    openwebui_conversation_id: str | None = None
    temporary_chat_id: str | None = None
    client_session_nonce: str | None = None
    first_message_hash: str | None = None
    title: str | None = None
    user_id: str | None = None
    source_app: str = "open_webui"
    created_at: str | None = None
    workspace_name: str = "Open WebUI"
    project_name: str = "Default"


class MessageCaptureRequest(BaseModel):
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


@router.post("/session/resolve", response_model=None)
def resolve_session(request: SessionResolveRequest) -> dict[str, Any]:
    return _resolve_session_payload(request)


def _resolve_session_payload(request: SessionResolveRequest) -> dict[str, Any]:
    settings = get_settings()

    def handler(connection: Any) -> dict[str, Any]:
        workspace_uuid = _default_workspace_uuid(connection, request.workspace_name)
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
    session_uuid = request.session_uuid
    if session_uuid is None:
        resolved = _resolve_session_payload(
            SessionResolveRequest(
                openwebui_conversation_id=request.openwebui_conversation_id,
                temporary_chat_id=request.temporary_chat_id,
                client_session_nonce=request.client_session_nonce,
                first_message_hash=request.first_message_hash,
                user_id=request.user_id,
                source_app=request.source_app,
                created_at=request.timestamp,
            )
        )
        session_uuid = str(resolved["session_uuid"])
    else:
        with _connection() as connection:
            if SessionRepository(connection).get_session(session_uuid) is None:
                raise HTTPException(status_code=404, detail="session not found")

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
        return {
            "session_uuid": result["session_uuid"],
            "message_uuid": result["message_uuid"],
            "duplicate": result["duplicate"],
        }
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.get("/sessions/{session_uuid}/lineage", response_model=None)
def session_lineage(session_uuid: str) -> dict[str, Any]:
    with _connection() as connection:
        session = SessionRepository(connection).get_session(session_uuid)
        if session is None:
            raise HTTPException(status_code=404, detail="session not found")
        return {
            "session_uuid": session_uuid,
            "openwebui_conversation_id": session.openwebui_conversation_id,
            "aliases": list_aliases(connection, session_uuid),
        }


@router.get("/status", response_model=None)
def integration_status() -> dict[str, Any]:
    settings = get_settings()
    with _connection() as connection:
        consistency = run_consistency_check(connection)
        last_attachment = connection.execute(
            """
            SELECT attachment_uuid, created_at, token_count
            FROM memory_context_attachments
            ORDER BY created_at DESC
            LIMIT 1
            """
        ).fetchone()
        last_error = connection.execute(
            """
            SELECT last_error
            FROM jobs
            WHERE last_error IS NOT NULL
            ORDER BY updated_at DESC
            LIMIT 1
            """
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
            "last_error": _sanitize_error(last_error["last_error"]) if last_error else None,
        }


@contextmanager
def _connection() -> Iterator[Any]:
    settings = get_settings()
    connection = connect(settings.db_path)
    try:
        apply_migrations(connection)
        yield connection
    finally:
        connection.close()


def _default_workspace_uuid(connection: Any, name: str) -> str:
    repository = WorkspaceRepository(connection)
    workspaces = repository.list_workspaces()
    if workspaces:
        return workspaces[0].workspace_uuid
    return repository.create_workspace(name).workspace_uuid


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
