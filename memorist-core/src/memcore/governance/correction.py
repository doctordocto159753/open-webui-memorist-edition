import sqlite3
from typing import Any

from memcore.active_memory.repositories import ActiveMemoryRepository
from memcore.governance.repositories import GovernanceRepository
from memcore.models import ChangeOperation, Memory, MemoryStatus, MemoryVersion, utc_now
from memcore.repositories.domain import RepositoryError
from memcore.repositories.memory_worker import MemoryStoreRepository
from memcore.retrieval.lexical import rebuild_index
from memcore.validators.ijson import dump_ijson, load_ijson


class MemoryCorrectionService:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self.repository = GovernanceRepository(connection)

    def impact_preview(self, memory_uuid: str | None) -> dict[str, Any]:
        if memory_uuid is None:
            return {"affected_memory_versions": [], "affected_block_versions": []}
        versions = [
            row["memory_version_uuid"]
            for row in self.connection.execute(
                "SELECT memory_version_uuid FROM memory_versions WHERE memory_uuid = ?",
                (memory_uuid,),
            )
        ]
        blocks = [
            dict(row)
            for row in self.connection.execute(
                """
                SELECT DISTINCT mb.block_uuid, mb.block_type, mb.label
                FROM memory_block_sources mbs
                JOIN memory_block_versions mbv ON mbv.block_version_uuid = mbs.block_version_uuid
                JOIN memory_blocks mb ON mb.block_uuid = mbv.block_uuid
                WHERE mbs.memory_uuid = ?
                """,
                (memory_uuid,),
            )
        ]
        attachments = [
            row["attachment_uuid"]
            for row in self.connection.execute(
                """
                SELECT attachment_uuid
                FROM memory_context_attachments
                WHERE source_memory_uuids_ijson LIKE ?
                """,
                (f"%{memory_uuid}%",),
            )
        ]
        return {
            "affected_memory_versions": versions,
            "affected_block_versions": blocks,
            "affected_retrieval_indexes": ["memory_version_fts", "memory_version_embeddings"],
            "affected_graph_nodes": [memory_uuid],
            "affected_attachments": attachments,
            "privacy_implications": [
                "past attachments remain audit records unless privacy erasure is requested"
            ],
        }

    def create_request(
        self,
        memory_uuid: str,
        requested_operation: str,
        actor_type: str,
        proposed_value: Any = None,
        reason: str | None = None,
        actor_uuid: str | None = None,
    ) -> dict[str, Any]:
        self._require_memory(memory_uuid)
        self._reject_protected(memory_uuid, actor_type)
        return self.repository.create_change_request(
            requested_operation=requested_operation,
            actor_type=actor_type,
            memory_uuid=memory_uuid,
            proposed_value=proposed_value,
            reason=reason,
            actor_uuid=actor_uuid,
            impact_preview=self.impact_preview(memory_uuid),
        )

    def apply_request(self, request_uuid: str) -> dict[str, Any]:
        request = self.repository.get_change_request(request_uuid)
        if request["status"] != "pending":
            return request
        memory_uuid = request["memory_uuid"]
        if memory_uuid is None:
            raise RepositoryError("change request has no memory_uuid")
        memory = self._require_memory(memory_uuid)
        operation = request["requested_operation"]
        proposed_value = load_ijson(request["proposed_value_ijson"])
        applied_version_uuid: str | None = None

        if operation in {"confirm", "correct", "restore_version"}:
            normalized_text = _normalized_text_for_operation(
                operation,
                proposed_value,
                _current_version(memory, self.connection),
            )
            applied_version_uuid = self._create_new_version(
                memory,
                normalized_text,
                operation,
                request["actor_type"],
                proposed_value,
            )
        elif operation in {"mark_incorrect", "mark_outdated", "retract"}:
            with self.connection:
                self.connection.execute(
                    "UPDATE memories SET status = ?, updated_at = ? WHERE memory_uuid = ?",
                    (
                        MemoryStatus.NEEDS_REVIEW.value
                        if operation != "retract"
                        else MemoryStatus.RETRACTED.value,
                        utc_now(),
                        memory_uuid,
                    ),
                )
            applied_version_uuid = memory.current_version_uuid
        elif operation in {"merge", "split", "change_scope", "change_sensitivity", "pin", "unpin"}:
            applied_version_uuid = self._create_new_version(
                memory,
                _current_version(memory, self.connection).normalized_text,
                operation,
                request["actor_type"],
                proposed_value,
            )
        else:
            raise RepositoryError(f"Unsupported change operation: {operation}")

        self._invalidate_dependencies(memory_uuid)
        rebuild_index(self.connection)
        updated = self.repository.update_change_request(
            request_uuid,
            {
                "status": "applied",
                "reviewed_at": utc_now(),
                "applied_at": utc_now(),
                "applied_memory_version_uuid": applied_version_uuid,
            },
        )
        return updated

    def cancel_request(self, request_uuid: str) -> dict[str, Any]:
        return self.repository.update_change_request(
            request_uuid,
            {"status": "cancelled", "reviewed_at": utc_now()},
        )

    def undo_request(self, request_uuid: str, actor_type: str = "user") -> dict[str, Any]:
        request = self.repository.get_change_request(request_uuid)
        memory_uuid = request["memory_uuid"]
        if memory_uuid is None:
            raise RepositoryError("change request has no memory_uuid")
        memory = self._require_memory(memory_uuid)
        versions = MemoryStoreRepository(self.connection).list_versions(memory_uuid)
        if len(versions) < 2:
            raise RepositoryError("no previous version available for undo")
        previous = versions[-2]
        undo_request = self.repository.create_change_request(
            requested_operation="restore_version",
            actor_type=actor_type,
            memory_uuid=memory_uuid,
            proposed_value={"normalized_text": previous.normalized_text},
            reason=f"undo {request_uuid}",
            impact_preview=self.impact_preview(memory_uuid),
        )
        self._create_new_version(
            memory,
            previous.normalized_text,
            "undo",
            actor_type,
            {"undone_request_uuid": request_uuid},
        )
        self._invalidate_dependencies(memory_uuid)
        return self.repository.update_change_request(
            undo_request["change_request_uuid"],
            {"status": "applied", "reviewed_at": utc_now(), "applied_at": utc_now()},
        )

    def _create_new_version(
        self,
        memory: Memory,
        normalized_text: str,
        operation: str,
        actor_type: str,
        proposed_value: Any,
    ) -> str:
        current = _current_version(memory, self.connection)
        new_version = MemoryVersion(
            memory_uuid=memory.memory_uuid,
            version_number=current.version_number + 1,
            normalized_text=normalized_text,
            value_ijson=dump_ijson({"value": normalized_text}),
            confidence=current.confidence,
            importance=current.importance,
            valid_from=current.valid_from,
            valid_until=current.valid_until,
            transaction_from=utc_now(),
            source_candidate_uuid=None,
            change_operation=ChangeOperation.UPDATE,
            change_reason_ijson=dump_ijson(
                {
                    "operation": operation,
                    "actor_type": actor_type,
                    "user_confirmed": operation == "confirm",
                    "proposed_value": proposed_value,
                }
            ),
        )
        with self.connection:
            self.connection.execute(
                "UPDATE memory_versions SET transaction_until = ? WHERE memory_version_uuid = ?",
                (utc_now(), current.memory_version_uuid),
            )
            self.connection.execute(
                """
                INSERT INTO memory_versions (
                    memory_version_uuid, memory_uuid, version_number, normalized_text,
                    value_ijson, confidence, importance, valid_from, valid_until,
                    transaction_from, transaction_until, source_candidate_uuid,
                    change_operation, change_reason_ijson, created_at, schema_version
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_version.memory_version_uuid,
                    new_version.memory_uuid,
                    new_version.version_number,
                    new_version.normalized_text,
                    new_version.value_ijson,
                    new_version.confidence,
                    new_version.importance,
                    new_version.valid_from,
                    new_version.valid_until,
                    new_version.transaction_from,
                    new_version.transaction_until,
                    new_version.source_candidate_uuid,
                    new_version.change_operation.value,
                    new_version.change_reason_ijson,
                    new_version.created_at,
                    new_version.schema_version,
                ),
            )
            self.connection.execute(
                """
                UPDATE memories
                SET current_version_uuid = ?, status = 'active', updated_at = ?
                WHERE memory_uuid = ?
                """,
                (new_version.memory_version_uuid, utc_now(), memory.memory_uuid),
            )
            self.connection.execute(
                "DELETE FROM memory_version_embeddings WHERE memory_version_uuid = ?",
                (current.memory_version_uuid,),
            )
            MemoryStoreRepository(self.connection).enqueue_projection(
                "memory",
                memory.memory_uuid,
                operation,
                {
                    "memory_uuid": memory.memory_uuid,
                    "memory_version_uuid": new_version.memory_version_uuid,
                    "operation": operation,
                },
            )
        return new_version.memory_version_uuid

    def _invalidate_dependencies(self, memory_uuid: str) -> None:
        active_repository = ActiveMemoryRepository(self.connection)
        block_rows = self.connection.execute(
            """
            SELECT DISTINCT mb.block_uuid
            FROM memory_block_sources mbs
            JOIN memory_block_versions mbv ON mbv.block_version_uuid = mbs.block_version_uuid
            JOIN memory_blocks mb ON mb.block_uuid = mbv.block_uuid
            WHERE mbs.memory_uuid = ?
            """,
            (memory_uuid,),
        ).fetchall()
        for row in block_rows:
            active_repository.mark_stale(row["block_uuid"], "source_memory_changed")
        with self.connection:
            self.connection.execute(
                """
                UPDATE memory_context_attachments
                SET status = 'stale', stale_reason = 'source_memory_changed'
                WHERE source_memory_uuids_ijson LIKE ?
                """,
                (f"%{memory_uuid}%",),
            )

    def _require_memory(self, memory_uuid: str) -> Memory:
        memory = MemoryStoreRepository(self.connection).get_memory(memory_uuid)
        if memory is None:
            raise RepositoryError(f"Memory not found: {memory_uuid}")
        return memory

    def _reject_protected(self, memory_uuid: str, actor_type: str) -> None:
        row = self.connection.execute(
            "SELECT scope_type, memory_type, sensitivity_class FROM memories WHERE memory_uuid = ?",
            (memory_uuid,),
        ).fetchone()
        if row is None:
            raise RepositoryError(f"Memory not found: {memory_uuid}")
        if row["scope_type"] == "admin" and actor_type not in {"admin", "system"}:
            raise RepositoryError("ordinary users cannot modify administrator policy")


def _current_version(memory: Memory, connection: sqlite3.Connection) -> MemoryVersion:
    if memory.current_version_uuid is None:
        raise RepositoryError("memory has no current version")
    row = connection.execute(
        "SELECT * FROM memory_versions WHERE memory_version_uuid = ?",
        (memory.current_version_uuid,),
    ).fetchone()
    if row is None:
        raise RepositoryError("current memory version not found")
    return MemoryVersion.model_validate(dict(row))


def _normalized_text_for_operation(
    operation: str,
    proposed_value: Any,
    current_version: MemoryVersion,
) -> str:
    if (
        operation == "restore_version"
        and isinstance(proposed_value, dict)
        and proposed_value.get("normalized_text")
    ):
        return str(proposed_value["normalized_text"])
    if operation == "confirm":
        return current_version.normalized_text
    if isinstance(proposed_value, dict):
        for key in ("normalized_text", "value", "text"):
            if proposed_value.get(key):
                return str(proposed_value[key])
    raise RepositoryError("proposed_value must contain normalized_text, value, or text")
