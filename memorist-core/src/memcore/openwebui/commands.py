from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

from memcore.model_control.repository import ModelControlRepository
from memcore.model_control.schemas import UsageEventCreate
from memcore.models import CreatorType, MessageRole, ModelRole, new_uuid, utc_now
from memcore.repositories import JobRepository, MessageRepository
from memcore.storage.commands import WriteResult


class CaptureIdempotencyConflict(ValueError):
    pass


@dataclass(frozen=True)
class CaptureOpenWebUIMessageCommand:
    session_uuid: str
    role: str
    content: str
    idempotency_key: str
    openwebui_conversation_id: str | None = None
    openwebui_message_id: str | None = None
    raw_payload: dict[str, Any] | None = None
    turn_index: int | None = None
    command_type: str = "openwebui_capture_message"
    priority: int = 100
    estimated_write_weight: int = 2
    max_transaction_ms: int = 5_000
    can_batch: bool = False

    def validate_idempotent_replay(self, connection: sqlite3.Connection) -> None:
        content_hash = sha256(self.content.encode("utf-8")).hexdigest()
        existing = connection.execute(
            """
            SELECT *
            FROM openwebui_message_captures
            WHERE idempotency_key = ?
            """,
            (self.idempotency_key,),
        ).fetchone()
        if existing is None:
            raise CaptureIdempotencyConflict("capture idempotency record is incomplete")
        fingerprint = (
            str(existing["session_uuid"]),
            str(existing["role"]),
            str(existing["content_hash"]),
            existing["openwebui_message_id"],
            existing["openwebui_conversation_id"],
        )
        expected = (
            self.session_uuid,
            self.role,
            content_hash,
            self.openwebui_message_id,
            self.openwebui_conversation_id,
        )
        if fingerprint != expected:
            raise CaptureIdempotencyConflict(
                "capture idempotency key reused with a different request"
            )

    def execute(self, connection: sqlite3.Connection) -> WriteResult:
        content_hash = sha256(self.content.encode("utf-8")).hexdigest()
        existing = connection.execute(
            """
            SELECT *
            FROM openwebui_message_captures
            WHERE idempotency_key = ?
            """,
            (self.idempotency_key,),
        ).fetchone()
        if existing is not None:
            self.validate_idempotent_replay(connection)
            return WriteResult(
                command_type=self.command_type,
                target_type="message",
                target_uuid=str(existing["message_uuid"]),
                result={
                    "session_uuid": existing["session_uuid"],
                    "message_uuid": existing["message_uuid"],
                    "duplicate": True,
                },
            )
        message = MessageRepository(connection).create_message(
            self.session_uuid,
            role=_message_role(self.role),
            creator_type=_creator_type(self.role),
            raw_text=self.content,
            raw_payload=self.raw_payload,
            turn_index=self.turn_index,
            job_priority=100,
        )
        with connection:
            connection.execute(
                """
                INSERT INTO openwebui_message_captures (
                    capture_uuid, idempotency_key, openwebui_conversation_id,
                    openwebui_message_id, role, content_hash, session_uuid,
                    message_uuid, created_at, schema_version
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                """,
                (
                    new_uuid(),
                    self.idempotency_key,
                    self.openwebui_conversation_id,
                    self.openwebui_message_id,
                    self.role,
                    content_hash,
                    self.session_uuid,
                    message.message_uuid,
                    utc_now(),
                ),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO openwebui_capture_dedup (
                    dedup_key, session_uuid, message_uuid, event_uuid, created_at
                )
                VALUES (?, ?, ?, NULL, ?)
                """,
                (self.idempotency_key, self.session_uuid, message.message_uuid, utc_now()),
            )
            model_control = ModelControlRepository(connection)
            extraction_default = model_control.resolve_default(ModelRole.MEMORY_EXTRACTION)
            extraction_profile_uuid = (
                str(extraction_default["model_profile_uuid"])
                if extraction_default is not None and extraction_default.get("model_profile_uuid")
                else None
            )
            job = JobRepository(connection).enqueue_job_once(
                "memory_extraction",
                {
                    "message_uuid": message.message_uuid,
                    "session_uuid": self.session_uuid,
                    "model_role": ModelRole.MEMORY_EXTRACTION.value,
                    "model_profile_uuid": extraction_profile_uuid,
                },
                priority=60,
            )
            model_control.record_usage_event(
                UsageEventCreate(
                    role=ModelRole.MEMORY_EXTRACTION,
                    stage="memory_extraction_queued",
                    model_profile_uuid=extraction_profile_uuid,
                    session_uuid=self.session_uuid,
                    message_uuid=message.message_uuid,
                    job_uuid=job.job_uuid,
                    status="queued",
                )
            )
        return WriteResult(
            command_type=self.command_type,
            target_type="message",
            target_uuid=message.message_uuid,
            result={
                "session_uuid": self.session_uuid,
                "message_uuid": message.message_uuid,
                "duplicate": False,
            },
        )


def _message_role(role: str) -> MessageRole:
    return MessageRole.USER if role == "user" else MessageRole.ASSISTANT


def _creator_type(role: str) -> CreatorType:
    return CreatorType.USER if role == "user" else CreatorType.MODEL
