from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from enum import StrEnum
from typing import Any, cast

from pydantic import BaseModel

from memcore.config import is_secret_key
from memcore.models import (
    AttachmentMode,
    CreatorType,
    Job,
    JobStatus,
    MemoryBlock,
    MemoryBlockType,
    MemoryContextAttachment,
    Message,
    MessageRole,
    MessageVersion,
    ModelProfile,
    ModelRole,
    ProcessingStatus,
    Project,
    Session,
    SessionEvent,
    SessionHotCache,
    SessionStatus,
    Workspace,
    new_uuid,
    utc_now,
)
from memcore.repositories.sqlite import SQLiteRepository
from memcore.storage.sqlite import with_busy_retry
from memcore.validators.ijson import canonical_hash_ijson, load_ijson
from memcore.validators.payload_policy import prepare_ijson_field


class RepositoryError(ValueError):
    """Raised when repository operations cannot satisfy domain contracts."""


class WorkspaceRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self.sqlite = SQLiteRepository(connection)

    def create_workspace(self, name: str, description: str | None = None) -> Workspace:
        workspace = Workspace(name=name, description=description)
        with self.connection:
            self.sqlite.insert("workspaces", _dump_model(workspace))
        return workspace

    def get_workspace(self, workspace_uuid: str) -> Workspace | None:
        return _fetch_one(
            self.connection,
            Workspace,
            "SELECT * FROM workspaces WHERE workspace_uuid = ?",
            (workspace_uuid,),
        )

    def list_workspaces(self) -> list[Workspace]:
        return _fetch_all(
            self.connection,
            Workspace,
            "SELECT * FROM workspaces ORDER BY created_at, workspace_uuid",
        )


class ProjectRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self.sqlite = SQLiteRepository(connection)

    def create_project(
        self,
        workspace_uuid: str,
        name: str,
        description: str | None = None,
    ) -> Project:
        project = Project(workspace_uuid=workspace_uuid, name=name, description=description)
        with self.connection:
            self.sqlite.insert("projects", _dump_model(project))
        return project

    def get_project(self, project_uuid: str) -> Project | None:
        return _fetch_one(
            self.connection,
            Project,
            "SELECT * FROM projects WHERE project_uuid = ?",
            (project_uuid,),
        )

    def list_projects(self, workspace_uuid: str | None = None) -> list[Project]:
        if workspace_uuid is None:
            return _fetch_all(
                self.connection,
                Project,
                "SELECT * FROM projects ORDER BY created_at, project_uuid",
            )

        return _fetch_all(
            self.connection,
            Project,
            "SELECT * FROM projects WHERE workspace_uuid = ? ORDER BY created_at, project_uuid",
            (workspace_uuid,),
        )


class SessionRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self.sqlite = SQLiteRepository(connection)

    def create_session(
        self,
        workspace_uuid: str | None = None,
        project_uuid: str | None = None,
        openwebui_conversation_id: str | None = None,
        title: str | None = None,
    ) -> Session:
        session = Session(
            workspace_uuid=workspace_uuid,
            project_uuid=project_uuid,
            openwebui_conversation_id=openwebui_conversation_id,
            title=title,
        )
        with self.connection:
            self.sqlite.insert("sessions", _dump_model(session))
        return session

    def get_session(self, session_uuid: str) -> Session | None:
        return _fetch_one(
            self.connection,
            Session,
            "SELECT * FROM sessions WHERE session_uuid = ?",
            (session_uuid,),
        )

    def update_session_state(
        self,
        session_uuid: str,
        conceptual_state_text: str | None = None,
        status: SessionStatus | str | None = None,
    ) -> Session:
        values: dict[str, Any] = {"updated_at": utc_now()}
        if conceptual_state_text is not None:
            values["conceptual_state_text"] = conceptual_state_text
        if status is not None:
            values["status"] = _enum_value(status)

        with self.connection:
            self.sqlite.update("sessions", values, "session_uuid = ?", (session_uuid,))

        session = self.get_session(session_uuid)
        if session is None:
            raise RepositoryError(f"Session not found: {session_uuid}")
        return session


class EventRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self.sqlite = SQLiteRepository(connection)

    def append_event(
        self,
        session_uuid: str,
        event_type: str,
        payload: Any,
        actor_type: str | None = None,
        actor_uuid: str | None = None,
    ) -> SessionEvent:
        with self.connection:
            return self._append_event(session_uuid, event_type, payload, actor_type, actor_uuid)

    def list_events(self, session_uuid: str) -> list[SessionEvent]:
        return _fetch_all(
            self.connection,
            SessionEvent,
            """
            SELECT * FROM session_events
            WHERE session_uuid = ?
            ORDER BY event_index
            """,
            (session_uuid,),
        )

    def _append_event(
        self,
        session_uuid: str,
        event_type: str,
        payload: Any,
        actor_type: str | None = None,
        actor_uuid: str | None = None,
    ) -> SessionEvent:
        payload_ijson = _canonical_ijson("payload_ijson", payload)
        payload_value = load_ijson(payload_ijson)
        next_index = self.connection.execute(
            """
            SELECT COALESCE(MAX(event_index), -1) + 1
            FROM session_events
            WHERE session_uuid = ?
            """,
            (session_uuid,),
        ).fetchone()[0]
        event = SessionEvent(
            session_uuid=session_uuid,
            event_type=event_type,
            event_index=int(next_index),
            payload_ijson=payload_ijson,
            actor_type=actor_type,
            actor_uuid=actor_uuid,
            content_hash=canonical_hash_ijson(payload_value),
        )
        self.sqlite.insert("session_events", _dump_model(event))
        return event


class MessageRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self.sqlite = SQLiteRepository(connection)
        self.events = EventRepository(connection)

    def create_message(
        self,
        session_uuid: str,
        role: MessageRole | str,
        creator_type: CreatorType | str,
        raw_text: str | None = None,
        raw_payload: Any = None,
        snapshot: Any = None,
        turn_index: int | None = None,
        model_profile_uuid: str | None = None,
        job_priority: int = 100,
        enqueue_text_unitization: bool = True,
    ) -> Message:
        if turn_index is None:
            turn_index = self._next_turn_index(session_uuid)

        message = Message(
            session_uuid=session_uuid,
            turn_index=turn_index,
            role=_message_role(role),
            creator_type=_creator_type(creator_type),
            model_profile_uuid=model_profile_uuid,
            raw_text=raw_text,
            raw_payload_ijson=_optional_ijson("raw_payload_ijson", raw_payload),
            snapshot_ijson=_optional_ijson("snapshot_ijson", snapshot),
        )
        with self.connection:
            self.sqlite.insert("messages", _dump_model(message))
            self.events._append_event(
                session_uuid,
                "message_created",
                {
                    "message_uuid": message.message_uuid,
                    "role": _enum_value(message.role),
                    "creator_type": _enum_value(message.creator_type),
                    "turn_index": message.turn_index,
                    "created_at": message.created_at,
                },
            )
            if enqueue_text_unitization:
                JobRepository(self.connection).enqueue_job_once(
                    "text_unitization",
                    {
                        "message_uuid": message.message_uuid,
                        "session_uuid": session_uuid,
                    },
                    priority=job_priority,
                )
        return message

    def get_message(self, message_uuid: str) -> Message | None:
        return _fetch_one(
            self.connection,
            Message,
            "SELECT * FROM messages WHERE message_uuid = ?",
            (message_uuid,),
        )

    def list_messages(self, session_uuid: str) -> list[Message]:
        return _fetch_all(
            self.connection,
            Message,
            """
            SELECT * FROM messages
            WHERE session_uuid = ?
            ORDER BY turn_index, created_at, message_uuid
            """,
            (session_uuid,),
        )

    def mark_processing_status(
        self,
        message_uuid: str,
        status: ProcessingStatus | str,
    ) -> Message:
        with self.connection:
            self.sqlite.update(
                "messages",
                {"processing_status": _enum_value(status), "updated_at": utc_now()},
                "message_uuid = ?",
                (message_uuid,),
            )
        message = self.get_message(message_uuid)
        if message is None:
            raise RepositoryError(f"Message not found: {message_uuid}")
        return message

    def soft_delete_message(self, message_uuid: str) -> Message:
        message = self.get_message(message_uuid)
        if message is None:
            raise RepositoryError(f"Message not found: {message_uuid}")

        with self.connection:
            self.sqlite.update(
                "messages",
                {
                    "is_deleted": 1,
                    "visibility": "deleted",
                    "updated_at": utc_now(),
                },
                "message_uuid = ?",
                (message_uuid,),
            )
            self.events._append_event(
                message.session_uuid,
                "message_deleted",
                {"message_uuid": message_uuid, "deleted_at": utc_now()},
            )

        deleted_message = self.get_message(message_uuid)
        if deleted_message is None:
            raise RepositoryError(f"Message not found after delete: {message_uuid}")
        return deleted_message

    def _next_turn_index(self, session_uuid: str) -> int:
        value = self.connection.execute(
            """
            SELECT COALESCE(MAX(turn_index), -1) + 1
            FROM messages
            WHERE session_uuid = ?
            """,
            (session_uuid,),
        ).fetchone()[0]
        return int(value)


class MessageVersionRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self.sqlite = SQLiteRepository(connection)
        self.events = EventRepository(connection)

    def create_version(
        self,
        message_uuid: str,
        raw_text: str | None = None,
        raw_payload: Any = None,
        snapshot: Any = None,
        change_reason: str | None = None,
        created_by: str | None = None,
    ) -> MessageVersion:
        message = MessageRepository(self.connection).get_message(message_uuid)
        if message is None:
            raise RepositoryError(f"Message not found: {message_uuid}")

        version = MessageVersion(
            message_uuid=message_uuid,
            version_number=self._next_version_number(message_uuid),
            raw_text=raw_text,
            raw_payload_ijson=_optional_ijson("raw_payload_ijson", raw_payload),
            snapshot_ijson=_optional_ijson("snapshot_ijson", snapshot),
            change_reason=change_reason,
            created_by=created_by,
        )
        with self.connection:
            self.sqlite.insert("message_versions", _dump_model(version))
            self.events._append_event(
                message.session_uuid,
                "message_version_created",
                {
                    "message_uuid": message_uuid,
                    "message_version_uuid": version.message_version_uuid,
                    "version_number": version.version_number,
                    "created_at": version.created_at,
                },
            )
        return version

    def list_versions(self, message_uuid: str) -> list[MessageVersion]:
        return _fetch_all(
            self.connection,
            MessageVersion,
            """
            SELECT * FROM message_versions
            WHERE message_uuid = ?
            ORDER BY version_number
            """,
            (message_uuid,),
        )

    def _next_version_number(self, message_uuid: str) -> int:
        value = self.connection.execute(
            """
            SELECT COALESCE(MAX(version_number), 0) + 1
            FROM message_versions
            WHERE message_uuid = ?
            """,
            (message_uuid,),
        ).fetchone()[0]
        return int(value)


class ModelProfileRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self.sqlite = SQLiteRepository(connection)

    def create_model_profile(
        self,
        provider: str,
        model_name: str,
        role: ModelRole | str,
        endpoint_url: str | None = None,
        is_local: bool = False,
        supports_structured_output: bool = False,
        supports_json_mode: bool = False,
        supports_embeddings: bool = False,
        pricing: Any = None,
        metadata: Any = None,
    ) -> ModelProfile:
        _reject_model_profile_secrets(pricing)
        _reject_model_profile_secrets(metadata)
        endpoint_has_secret = endpoint_url is not None and any(
            part in endpoint_url.lower() for part in ("key=", "token=")
        )
        if endpoint_has_secret:
            raise RepositoryError("endpoint_url must not contain API keys or tokens")

        profile = ModelProfile(
            provider=provider,
            model_name=model_name,
            role=_model_role(role),
            endpoint_url=endpoint_url,
            is_local=is_local,
            supports_structured_output=supports_structured_output,
            supports_json_mode=supports_json_mode,
            supports_embeddings=supports_embeddings,
            pricing_ijson=_optional_ijson("pricing_ijson", pricing),
            metadata_ijson=_optional_ijson("metadata_ijson", metadata),
        )
        with self.connection:
            self.sqlite.insert("model_profiles", _dump_model(profile))
        return profile

    def get_model_profile(self, model_profile_uuid: str) -> ModelProfile | None:
        return _fetch_one(
            self.connection,
            ModelProfile,
            "SELECT * FROM model_profiles WHERE model_profile_uuid = ?",
            (model_profile_uuid,),
        )

    def list_model_profiles(self, role: ModelRole | str | None = None) -> list[ModelProfile]:
        if role is None:
            return _fetch_all(
                self.connection,
                ModelProfile,
                "SELECT * FROM model_profiles ORDER BY created_at, model_profile_uuid",
            )
        return _fetch_all(
            self.connection,
            ModelProfile,
            """
            SELECT * FROM model_profiles
            WHERE role = ?
            ORDER BY created_at, model_profile_uuid
            """,
            (_enum_value(role),),
        )


class JobRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self.sqlite = SQLiteRepository(connection)

    def enqueue_job(
        self,
        job_type: str,
        payload: Any,
        priority: int = 50,
        run_after: str | None = None,
        max_attempts: int = 3,
    ) -> Job:
        job = Job(
            job_type=job_type,
            payload_ijson=_canonical_ijson("payload_ijson", payload),
            idempotency_key=None,
            priority=priority,
            run_after=run_after,
            max_attempts=max_attempts,
        )
        with self.connection:
            self.sqlite.insert("jobs", _dump_model(job))
        return job

    def enqueue_job_once(
        self,
        job_type: str,
        payload: Any,
        priority: int = 50,
        run_after: str | None = None,
        max_attempts: int = 3,
    ) -> Job:
        payload_ijson = _canonical_ijson("payload_ijson", payload)
        existing = _fetch_one(
            self.connection,
            Job,
            """
            SELECT * FROM jobs
            WHERE job_type = ? AND payload_ijson = ?
            ORDER BY created_at
            LIMIT 1
            """,
            (job_type, payload_ijson),
        )
        if existing is not None:
            return existing

        job = Job(
            job_type=job_type,
            payload_ijson=payload_ijson,
            idempotency_key=f"{job_type}:{canonical_hash_ijson(load_ijson(payload_ijson))}",
            priority=priority,
            run_after=run_after,
            max_attempts=max_attempts,
        )
        self.sqlite.insert("jobs", _dump_model(job))
        return job

    def get_job(self, job_uuid: str) -> Job | None:
        return _fetch_one(
            self.connection,
            Job,
            "SELECT * FROM jobs WHERE job_uuid = ?",
            (job_uuid,),
        )

    def claim_next_job(self) -> Job | None:
        return with_busy_retry(self._claim_next_job_once)

    def _claim_next_job_once(self) -> Job | None:
        now = utc_now()
        with self.connection:
            try:
                row = self.connection.execute(
                    """
                    UPDATE jobs
                    SET status = ?,
                        locked_by = ?,
                        locked_at = ?,
                        attempts = attempts + 1,
                        updated_at = ?
                    WHERE job_uuid = (
                        SELECT job_uuid
                        FROM jobs
                        WHERE status = ?
                          AND (run_after IS NULL OR run_after <= ?)
                        ORDER BY priority DESC, created_at ASC, job_uuid ASC
                        LIMIT 1
                    )
                    AND status = ?
                    RETURNING *
                    """,
                    (
                        JobStatus.RUNNING.value,
                        "sqlite-write-actor",
                        now,
                        now,
                        JobStatus.PENDING.value,
                        now,
                        JobStatus.PENDING.value,
                    ),
                ).fetchone()
            except sqlite3.OperationalError as error:
                if "returning" not in str(error).lower():
                    raise
                row = self._claim_next_job_compare_and_swap(now)
        if row is None:
            return None
        return Job.model_validate(dict(row))

    def _claim_next_job_compare_and_swap(self, now: str) -> sqlite3.Row | None:
        row = self.connection.execute(
            """
            SELECT * FROM jobs
            WHERE status = ?
              AND (run_after IS NULL OR run_after <= ?)
            ORDER BY priority DESC, created_at ASC, job_uuid ASC
            LIMIT 1
            """,
            (JobStatus.PENDING.value, now),
        ).fetchone()
        if row is None:
            return None
        cursor = self.sqlite.update(
            "jobs",
            {
                "status": JobStatus.RUNNING.value,
                "locked_by": "sqlite-write-actor",
                "locked_at": now,
                "attempts": int(row["attempts"]) + 1,
                "updated_at": now,
            },
            "job_uuid = ? AND status = ?",
            (str(row["job_uuid"]), JobStatus.PENDING.value),
        )
        if cursor.rowcount != 1:
            return None
        claimed_row = self.connection.execute(
            "SELECT * FROM jobs WHERE job_uuid = ?",
            (str(row["job_uuid"]),),
        ).fetchone()
        return cast(sqlite3.Row | None, claimed_row)

    def mark_job_succeeded(self, job_uuid: str) -> Job:
        return self._update_job(
            job_uuid,
            {
                "status": JobStatus.SUCCEEDED.value,
                "locked_by": None,
                "locked_at": None,
                "updated_at": utc_now(),
            },
        )

    def mark_job_failed(self, job_uuid: str, error: str, retry: bool = True) -> Job:
        job = self.get_job(job_uuid)
        if job is None:
            raise RepositoryError(f"Job not found: {job_uuid}")

        if retry and job.attempts < job.max_attempts:
            status = JobStatus.PENDING
        elif retry:
            status = JobStatus.DEAD
        else:
            status = JobStatus.FAILED

        return self._update_job(
            job_uuid,
            {
                "status": status.value,
                "last_error": error,
                "last_error_sanitized": error[:240],
                "locked_by": None,
                "locked_at": None,
                "updated_at": utc_now(),
            },
        )

    def mark_job_dead(self, job_uuid: str, error: str) -> Job:
        return self._update_job(
            job_uuid,
            {
                "status": JobStatus.DEAD.value,
                "last_error": error,
                "last_error_sanitized": error[:240],
                "locked_by": None,
                "locked_at": None,
                "updated_at": utc_now(),
            },
        )

    def _update_job(self, job_uuid: str, values: Mapping[str, Any]) -> Job:
        with self.connection:
            self.sqlite.update("jobs", values, "job_uuid = ?", (job_uuid,))
        job = self.get_job(job_uuid)
        if job is None:
            raise RepositoryError(f"Job not found: {job_uuid}")
        return job


class MemoryContextAttachmentRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self.sqlite = SQLiteRepository(connection)

    def create_attachment(
        self,
        session_uuid: str,
        attachment_mode: AttachmentMode | str,
        ijson_attachment: Any,
        attachment_uuid: str | None = None,
        project_uuid: str | None = None,
        input_message_uuid: str | None = None,
        retrieval_run_uuid: str | None = None,
        rendered_attachment: str | None = None,
        source_block_uuids: Any = None,
        source_memory_uuids: Any = None,
        source_fact_edge_uuids: Any = None,
        token_count: int = 0,
        confidence: float = 0.5,
        abstention_status: str | None = None,
    ) -> MemoryContextAttachment:
        attachment = MemoryContextAttachment(
            attachment_uuid=attachment_uuid or new_uuid(),
            session_uuid=session_uuid,
            project_uuid=project_uuid,
            input_message_uuid=input_message_uuid,
            retrieval_run_uuid=retrieval_run_uuid,
            attachment_mode=_attachment_mode(attachment_mode),
            ijson_attachment=_canonical_ijson("ijson_attachment", ijson_attachment),
            rendered_attachment=rendered_attachment,
            source_block_uuids_ijson=_optional_ijson(
                "source_block_uuids_ijson", source_block_uuids
            ),
            source_memory_uuids_ijson=_optional_ijson(
                "source_memory_uuids_ijson", source_memory_uuids
            ),
            source_fact_edge_uuids_ijson=_optional_ijson(
                "source_fact_edge_uuids_ijson", source_fact_edge_uuids
            ),
            token_count=token_count,
            confidence=confidence,
            abstention_status=abstention_status,
        )
        with self.connection:
            self.sqlite.insert("memory_context_attachments", _dump_model(attachment))
        return attachment

    def get_attachment(self, attachment_uuid: str) -> MemoryContextAttachment | None:
        return _fetch_one(
            self.connection,
            MemoryContextAttachment,
            "SELECT * FROM memory_context_attachments WHERE attachment_uuid = ?",
            (attachment_uuid,),
        )

    def list_attachments(self, session_uuid: str) -> list[MemoryContextAttachment]:
        return _fetch_all(
            self.connection,
            MemoryContextAttachment,
            """
            SELECT * FROM memory_context_attachments
            WHERE session_uuid = ?
            ORDER BY created_at, attachment_uuid
            """,
            (session_uuid,),
        )


class MemoryBlockRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self.sqlite = SQLiteRepository(connection)

    def create_block(
        self,
        block_type: MemoryBlockType | str,
        scope_type: str,
        label: str,
        value: str,
        char_limit: int,
        scope_uuid: str | None = None,
        description: str | None = None,
        priority: int = 50,
        read_only: bool = False,
        always_in_context: bool = True,
        source_memory_uuids: Any = None,
        source_nucleus_uuids: Any = None,
        source_fact_edge_uuids: Any = None,
    ) -> MemoryBlock:
        block = MemoryBlock(
            block_type=_memory_block_type(block_type),
            scope_type=scope_type,
            scope_uuid=scope_uuid,
            label=label,
            description=description,
            value=value,
            char_limit=char_limit,
            priority=priority,
            read_only=read_only,
            always_in_context=always_in_context,
            source_memory_uuids_ijson=_optional_ijson(
                "source_memory_uuids_ijson", source_memory_uuids
            ),
            source_nucleus_uuids_ijson=_optional_ijson(
                "source_nucleus_uuids_ijson", source_nucleus_uuids
            ),
            source_fact_edge_uuids_ijson=_optional_ijson(
                "source_fact_edge_uuids_ijson", source_fact_edge_uuids
            ),
        )
        with self.connection:
            self.sqlite.insert("memory_blocks", _dump_model(block))
        return block

    def get_block(self, block_uuid: str) -> MemoryBlock | None:
        return _fetch_one(
            self.connection,
            MemoryBlock,
            "SELECT * FROM memory_blocks WHERE block_uuid = ?",
            (block_uuid,),
        )

    def list_blocks(
        self,
        scope_type: str | None = None,
        scope_uuid: str | None = None,
        block_type: MemoryBlockType | str | None = None,
    ) -> list[MemoryBlock]:
        clauses: list[str] = []
        params: list[Any] = []
        if scope_type is not None:
            clauses.append("scope_type = ?")
            params.append(scope_type)
        if scope_uuid is not None:
            clauses.append("scope_uuid = ?")
            params.append(scope_uuid)
        if block_type is not None:
            clauses.append("block_type = ?")
            params.append(_enum_value(block_type))

        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        return _fetch_all(
            self.connection,
            MemoryBlock,
            f"SELECT * FROM memory_blocks {where_sql} ORDER BY priority DESC, created_at",
            tuple(params),
        )


class SessionHotCacheRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self.sqlite = SQLiteRepository(connection)

    def upsert_cache(
        self,
        session_uuid: str,
        current_problem: str | None = None,
        last_user_intent: str | None = None,
        active_constraints: Any = None,
        unresolved_questions: Any = None,
        recent_entities: Any = None,
        current_topic_nucleus_uuid: str | None = None,
        recent_memory_uuids: Any = None,
        current_plan: str | None = None,
        checkpoint_event_uuid: str | None = None,
    ) -> SessionHotCache:
        cache = SessionHotCache(
            session_uuid=session_uuid,
            current_problem=current_problem,
            last_user_intent=last_user_intent,
            active_constraints_ijson=_optional_ijson(
                "active_constraints_ijson", active_constraints
            ),
            unresolved_questions_ijson=_optional_ijson(
                "unresolved_questions_ijson", unresolved_questions
            ),
            recent_entities_ijson=_optional_ijson("recent_entities_ijson", recent_entities),
            current_topic_nucleus_uuid=current_topic_nucleus_uuid,
            recent_memory_uuids_ijson=_optional_ijson(
                "recent_memory_uuids_ijson", recent_memory_uuids
            ),
            current_plan=current_plan,
            checkpoint_event_uuid=checkpoint_event_uuid,
            last_updated_at=utc_now(),
        )
        values = _dump_model(cache)
        update_assignments = ", ".join(
            f"{column} = excluded.{column}" for column in values if column != "session_uuid"
        )
        columns = ", ".join(values)
        placeholders = ", ".join("?" for _ in values)
        sql = (
            f"INSERT INTO session_hot_cache ({columns}) VALUES ({placeholders}) "
            f"ON CONFLICT(session_uuid) DO UPDATE SET {update_assignments}"
        )
        with self.connection:
            self.connection.execute(sql, tuple(values.values()))
        return cache

    def get_cache(self, session_uuid: str) -> SessionHotCache | None:
        return _fetch_one(
            self.connection,
            SessionHotCache,
            "SELECT * FROM session_hot_cache WHERE session_uuid = ?",
            (session_uuid,),
        )


def _dump_model(model: BaseModel) -> dict[str, Any]:
    return model.model_dump(mode="json")


def _fetch_one[ModelT: BaseModel](
    connection: sqlite3.Connection,
    model_type: type[ModelT],
    sql: str,
    params: tuple[Any, ...] = (),
) -> ModelT | None:
    row = connection.execute(sql, params).fetchone()
    if row is None:
        return None
    return model_type.model_validate(dict(row))


def _fetch_all[ModelT: BaseModel](
    connection: sqlite3.Connection,
    model_type: type[ModelT],
    sql: str,
    params: tuple[Any, ...] = (),
) -> list[ModelT]:
    return [model_type.model_validate(dict(row)) for row in connection.execute(sql, params)]


def _canonical_ijson(field_name: str, value: Any) -> str:
    canonical_value = prepare_ijson_field(field_name, value)
    if canonical_value is None:
        raise RepositoryError(f"{field_name} is required")
    return canonical_value


def _optional_ijson(field_name: str, value: Any) -> str | None:
    if value is None:
        return None
    return prepare_ijson_field(field_name, value)


def _enum_value(value: EnumLike) -> str:
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, str):
        return value
    return str(value.value)


def _message_role(value: MessageRole | str) -> MessageRole:
    return value if isinstance(value, MessageRole) else MessageRole(value)


def _creator_type(value: CreatorType | str) -> CreatorType:
    return value if isinstance(value, CreatorType) else CreatorType(value)


def _model_role(value: ModelRole | str) -> ModelRole:
    return value if isinstance(value, ModelRole) else ModelRole(value)


def _attachment_mode(value: AttachmentMode | str) -> AttachmentMode:
    return value if isinstance(value, AttachmentMode) else AttachmentMode(value)


def _memory_block_type(value: MemoryBlockType | str) -> MemoryBlockType:
    return value if isinstance(value, MemoryBlockType) else MemoryBlockType(value)


def _reject_model_profile_secrets(value: Any) -> None:
    if value is None:
        return
    if isinstance(value, str):
        value = load_ijson(value)

    if isinstance(value, Mapping):
        for key, child_value in value.items():
            if is_secret_key(str(key)):
                raise RepositoryError(f"model profile metadata must not contain secret key: {key}")
            _reject_model_profile_secrets(child_value)
    elif isinstance(value, list):
        for child_value in value:
            _reject_model_profile_secrets(child_value)


type EnumLike = (
    AttachmentMode
    | CreatorType
    | JobStatus
    | MemoryBlockType
    | MessageRole
    | ModelRole
    | ProcessingStatus
    | SessionStatus
    | str
)
