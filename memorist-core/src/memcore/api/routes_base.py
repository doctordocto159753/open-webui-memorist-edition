from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from memcore.config import get_settings
from memcore.models import CreatorType, MessageRole, SessionStatus
from memcore.openwebui.session_resolution import list_aliases
from memcore.repositories import (
    MessageRepository,
    ProjectRepository,
    SessionRepository,
)
from memcore.repositories.domain import (
    EventRepository,
    MessageVersionRepository,
    WorkspaceRepository,
)
from memcore.storage.migrations import apply_migrations
from memcore.storage.sqlite import connect

router = APIRouter(prefix="/memcore", tags=["Base APIs"])


class WorkspaceCreateRequest(BaseModel):
    name: str = Field(min_length=1)
    description: str | None = None


class ProjectCreateRequest(BaseModel):
    workspace_uuid: str
    name: str = Field(min_length=1)
    description: str | None = None


class SessionCreateRequest(BaseModel):
    workspace_uuid: str | None = None
    project_uuid: str | None = None
    openwebui_conversation_id: str | None = None
    title: str | None = None


class SessionPatchRequest(BaseModel):
    conceptual_state_text: str | None = None
    status: str | None = None


class MessageCreateRequest(BaseModel):
    session_uuid: str
    role: str
    creator_type: str
    raw_text: str | None = None
    raw_payload: dict[str, Any] | None = None
    snapshot: dict[str, Any] | None = None


@router.post("/workspaces", response_model=None)
def create_workspace(request: WorkspaceCreateRequest) -> dict[str, Any]:
    with _connection() as connection:
        return WorkspaceRepository(connection).create_workspace(
            request.name,
            request.description,
        ).model_dump(mode="json")


@router.get("/workspaces", response_model=None)
def list_workspaces(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    with _connection() as connection:
        rows = connection.execute(
            "SELECT * FROM workspaces ORDER BY created_at, workspace_uuid LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        return {"items": [dict(row) for row in rows], "limit": limit, "offset": offset}


@router.get("/workspaces/{workspace_uuid}", response_model=None)
def get_workspace(workspace_uuid: str) -> dict[str, Any]:
    with _connection() as connection:
        workspace = WorkspaceRepository(connection).get_workspace(workspace_uuid)
        if workspace is None:
            raise HTTPException(status_code=404, detail="workspace not found")
        return workspace.model_dump(mode="json")


@router.post("/projects", response_model=None)
def create_project(request: ProjectCreateRequest) -> dict[str, Any]:
    with _connection() as connection:
        if WorkspaceRepository(connection).get_workspace(request.workspace_uuid) is None:
            raise HTTPException(status_code=404, detail="workspace not found")
        return ProjectRepository(connection).create_project(
            request.workspace_uuid,
            request.name,
            request.description,
        ).model_dump(mode="json")


@router.get("/projects", response_model=None)
def list_projects(
    workspace_uuid: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    where = "WHERE workspace_uuid = ?" if workspace_uuid else ""
    params: tuple[Any, ...] = (workspace_uuid, limit, offset) if workspace_uuid else (limit, offset)
    with _connection() as connection:
        rows = connection.execute(
            f"SELECT * FROM projects {where} ORDER BY created_at, project_uuid LIMIT ? OFFSET ?",
            params,
        ).fetchall()
        return {"items": [dict(row) for row in rows], "limit": limit, "offset": offset}


@router.get("/projects/{project_uuid}", response_model=None)
def get_project(project_uuid: str) -> dict[str, Any]:
    with _connection() as connection:
        project = ProjectRepository(connection).get_project(project_uuid)
        if project is None:
            raise HTTPException(status_code=404, detail="project not found")
        return project.model_dump(mode="json")


@router.post("/sessions", response_model=None)
def create_session(request: SessionCreateRequest) -> dict[str, Any]:
    with _connection() as connection:
        return SessionRepository(connection).create_session(
            workspace_uuid=request.workspace_uuid,
            project_uuid=request.project_uuid,
            openwebui_conversation_id=request.openwebui_conversation_id,
            title=request.title,
        ).model_dump(mode="json")


@router.get("/sessions", response_model=None)
def list_sessions(
    workspace_uuid: str | None = None,
    project_uuid: str | None = None,
    status: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    clauses = []
    params: list[Any] = []
    if workspace_uuid:
        clauses.append("workspace_uuid = ?")
        params.append(workspace_uuid)
    if project_uuid:
        clauses.append("project_uuid = ?")
        params.append(project_uuid)
    if status:
        clauses.append("status = ?")
        params.append(status)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with _connection() as connection:
        rows = connection.execute(
            f"SELECT * FROM sessions {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            tuple(params) + (limit, offset),
        ).fetchall()
        return {"items": [dict(row) for row in rows], "limit": limit, "offset": offset}


@router.get("/sessions/{session_uuid}", response_model=None)
def get_session(session_uuid: str) -> dict[str, Any]:
    with _connection() as connection:
        session = SessionRepository(connection).get_session(session_uuid)
        if session is None:
            raise HTTPException(status_code=404, detail="session not found")
        return session.model_dump(mode="json")


@router.patch("/sessions/{session_uuid}", response_model=None)
def patch_session(session_uuid: str, request: SessionPatchRequest) -> dict[str, Any]:
    with _connection() as connection:
        try:
            status = SessionStatus(request.status) if request.status is not None else None
            return SessionRepository(connection).update_session_state(
                session_uuid,
                conceptual_state_text=request.conceptual_state_text,
                status=status,
            ).model_dump(mode="json")
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error


@router.get("/sessions/{session_uuid}/messages", response_model=None)
def list_session_messages(
    session_uuid: str,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    with _connection() as connection:
        if SessionRepository(connection).get_session(session_uuid) is None:
            raise HTTPException(status_code=404, detail="session not found")
        rows = connection.execute(
            """
            SELECT *
            FROM messages
            WHERE session_uuid = ?
            ORDER BY turn_index, created_at, message_uuid
            LIMIT ? OFFSET ?
            """,
            (session_uuid, limit, offset),
        ).fetchall()
        return {"items": [dict(row) for row in rows], "limit": limit, "offset": offset}


@router.get("/sessions/{session_uuid}/events", response_model=None)
def list_session_events(
    session_uuid: str,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    with _connection() as connection:
        rows = connection.execute(
            """
            SELECT *
            FROM session_events
            WHERE session_uuid = ?
            ORDER BY event_index
            LIMIT ? OFFSET ?
            """,
            (session_uuid, limit, offset),
        ).fetchall()
        return {"items": [dict(row) for row in rows], "limit": limit, "offset": offset}


@router.get("/sessions/{session_uuid}/aliases", response_model=None)
def list_session_aliases(session_uuid: str) -> dict[str, Any]:
    with _connection() as connection:
        if SessionRepository(connection).get_session(session_uuid) is None:
            raise HTTPException(status_code=404, detail="session not found")
        return {"items": list_aliases(connection, session_uuid)}


@router.post("/messages", response_model=None)
def create_message(request: MessageCreateRequest) -> dict[str, Any]:
    with _connection() as connection:
        if SessionRepository(connection).get_session(request.session_uuid) is None:
            raise HTTPException(status_code=404, detail="session not found")
        try:
            message = MessageRepository(connection).create_message(
                request.session_uuid,
                role=MessageRole(request.role),
                creator_type=CreatorType(request.creator_type),
                raw_text=request.raw_text,
                raw_payload=request.raw_payload,
                snapshot=request.snapshot,
                job_priority=100,
            )
            return message.model_dump(mode="json")
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error


@router.get("/messages/{message_uuid}", response_model=None)
def get_message(message_uuid: str) -> dict[str, Any]:
    with _connection() as connection:
        message = MessageRepository(connection).get_message(message_uuid)
        if message is None:
            raise HTTPException(status_code=404, detail="message not found")
        return message.model_dump(mode="json")


@router.get("/messages/{message_uuid}/lineage", response_model=None)
def message_lineage(message_uuid: str) -> dict[str, Any]:
    with _connection() as connection:
        message = MessageRepository(connection).get_message(message_uuid)
        if message is None:
            raise HTTPException(status_code=404, detail="message not found")
        session = SessionRepository(connection).get_session(message.session_uuid)
        versions = MessageVersionRepository(connection).list_versions(message_uuid)
        events = EventRepository(connection).list_events(message.session_uuid)
        text_units = _rows(
            connection,
            "SELECT * FROM text_units WHERE message_uuid = ?",
            (message_uuid,),
        )
        unit_uuids = [str(row["text_unit_uuid"]) for row in text_units]
        return {
            "session": session.model_dump(mode="json") if session else None,
            "message": message.model_dump(mode="json"),
            "message_versions": [version.model_dump(mode="json") for version in versions],
            "events": [event.model_dump(mode="json") for event in events],
            "text_units": text_units,
            "gate_decisions": _rows_for_ids(
                connection,
                "memory_gate_decisions",
                "text_unit_uuid",
                unit_uuids,
            ),
            "analyses": _rows_for_ids(
                connection,
                "linguistic_analyses",
                "text_unit_uuid",
                unit_uuids,
            ),
            "candidates": _rows_for_ids(
                connection,
                "memory_candidates",
                "text_unit_uuid",
                unit_uuids,
            ),
            "memories": [],
            "attachments": _rows(
                connection,
                "SELECT * FROM memory_context_attachments WHERE input_message_uuid = ?",
                (message_uuid,),
            ),
            "delivery_events": _rows(
                connection,
                "SELECT * FROM memory_delivery_events WHERE response_message_uuid = ?",
                (message_uuid,),
            ),
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


def _rows(connection: Any, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    return [dict(row) for row in connection.execute(sql, params)]


def _rows_for_ids(
    connection: Any,
    table_name: str,
    column_name: str,
    values: list[str],
) -> list[dict[str, Any]]:
    if not values:
        return []
    placeholders = ",".join("?" for _ in values)
    return _rows(
        connection,
        f"SELECT * FROM {table_name} WHERE {column_name} IN ({placeholders})",
        tuple(values),
    )
