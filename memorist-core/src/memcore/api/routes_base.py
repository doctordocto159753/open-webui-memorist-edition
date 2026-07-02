from __future__ import annotations

import importlib
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from memcore.config import Settings, get_settings
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
    settings = get_settings()
    if _is_full_postgres(settings):
        with _pg_connection(settings) as connection:
            message = connection.execute(
                "SELECT * FROM messages WHERE message_uuid = %s",
                (message_uuid,),
            ).fetchone()
            if message is None:
                raise HTTPException(status_code=404, detail="message not found")
            return _jsonable_dict(message)
    with _connection() as connection:
        message = MessageRepository(connection).get_message(message_uuid)
        if message is None:
            raise HTTPException(status_code=404, detail="message not found")
        return message.model_dump(mode="json")


@router.get("/messages/{message_uuid}/lineage", response_model=None)
def message_lineage(message_uuid: str) -> dict[str, Any]:
    settings = get_settings()
    if _is_full_postgres(settings):
        return _pg_message_lineage(settings, message_uuid)
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


def _is_full_postgres(settings: Settings) -> bool:
    return settings.runtime_profile == "full" and settings.canonical_store == "postgres"


@contextmanager
def _pg_connection(settings: Settings) -> Iterator[Any]:
    if not settings.postgres_dsn:
        raise RuntimeError("postgres_dsn is required for Full Mode base routes")
    psycopg = importlib.import_module("psycopg")
    rows = importlib.import_module("psycopg.rows")
    connection = psycopg.connect(settings.postgres_dsn, row_factory=rows.dict_row)
    try:
        yield connection
    finally:
        connection.close()


def _pg_message_lineage(settings: Settings, message_uuid: str) -> dict[str, Any]:
    with _pg_connection(settings) as connection:
        message = connection.execute(
            "SELECT * FROM messages WHERE message_uuid = %s",
            (message_uuid,),
        ).fetchone()
        if message is None:
            raise HTTPException(status_code=404, detail="message not found")
        session = connection.execute(
            "SELECT * FROM sessions WHERE session_uuid = %s",
            (message["session_uuid"],),
        ).fetchone()
        text_units = _pg_rows(
            connection,
            "SELECT * FROM text_units WHERE message_uuid = %s ORDER BY unit_index",
            (message_uuid,),
        )
        unit_uuids = [str(row["text_unit_uuid"]) for row in text_units]
        candidates = _pg_rows_for_values(
            connection,
            "memory_candidates",
            "text_unit_uuid",
            unit_uuids,
        )
        attachments = _pg_rows(
            connection,
            "SELECT * FROM memory_context_attachments WHERE input_message_uuid = %s",
            (message_uuid,),
        )
        events = _pg_rows(
            connection,
            "SELECT * FROM session_events WHERE session_uuid = %s ORDER BY event_index",
            (message["session_uuid"],),
        )
        return {
            "session": _jsonable_dict(session) if session else None,
            "message": _jsonable_dict(message),
            "message_versions": _pg_rows(
                connection,
                "SELECT * FROM message_versions WHERE message_uuid = %s ORDER BY version_number",
                (message_uuid,),
            ),
            "events": events,
            "text_units": text_units,
            "gate_decisions": [],
            "analyses": [],
            "candidates": candidates,
            "memories": _pg_memories_for_units(connection, unit_uuids),
            "attachments": attachments,
            "delivery_events": [],
        }


def _pg_memories_for_units(connection: Any, unit_uuids: list[str]) -> list[dict[str, Any]]:
    if not unit_uuids:
        return []
    placeholders = ",".join("%s" for _ in unit_uuids)
    return _pg_rows(
        connection,
        f"""
        SELECT DISTINCT m.*
        FROM memories m
        JOIN memory_evidence_links mel ON mel.memory_uuid = m.memory_uuid
        JOIN memory_candidates mc ON mc.candidate_uuid = mel.candidate_uuid
        WHERE mc.text_unit_uuid IN ({placeholders})
        ORDER BY m.created_at
        """,
        tuple(unit_uuids),
    )


def _pg_rows(connection: Any, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    return [_jsonable_dict(row) for row in connection.execute(sql, params).fetchall()]


def _pg_rows_for_values(
    connection: Any,
    table_name: str,
    column_name: str,
    values: list[str],
) -> list[dict[str, Any]]:
    if not values:
        return []
    placeholders = ",".join("%s" for _ in values)
    return _pg_rows(
        connection,
        f"SELECT * FROM {table_name} WHERE {column_name} IN ({placeholders})",
        tuple(values),
    )


def _jsonable_dict(row: Any) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in dict(row).items():
        if hasattr(value, "isoformat"):
            result[str(key)] = value.isoformat()
        else:
            result[str(key)] = value
    return result


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
