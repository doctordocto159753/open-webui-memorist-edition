from __future__ import annotations

import json
import sqlite3
from collections import Counter
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any, Protocol

from pydantic import BaseModel

from memcore.governance.repositories import GovernanceRepository, hash_token
from memcore.models import new_uuid, utc_now
from memcore.repositories.domain import RepositoryError
from memcore.validators.ijson import canonical_hash_ijson, dump_ijson


class PrivacyRecord(BaseModel):
    storage_type: str
    record_type: str
    record_uuid: str | None
    action: str


class ErasureResult(BaseModel):
    erased_counts: dict[str, int]
    retained_counts: dict[str, int]
    exceptions: list[dict[str, object]]


class VerificationResult(BaseModel):
    passed: bool
    checks: dict[str, object]


class PrivacyStorageAdapter(Protocol):
    def discover(self, request: dict[str, Any]) -> list[PrivacyRecord]: ...

    def quarantine(self, records: list[PrivacyRecord]) -> None: ...

    def erase(self, records: list[PrivacyRecord], request_uuid: str) -> ErasureResult: ...

    def verify(self, records: list[PrivacyRecord]) -> VerificationResult: ...


class SQLiteCanonicalAdapter:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def discover(self, request: dict[str, Any]) -> list[PrivacyRecord]:
        target_type = request["target_type"]
        target_uuid = request["target_uuid"]
        if target_type == "memory" and target_uuid:
            return [
                PrivacyRecord(
                    storage_type="sqlite",
                    record_type="memory",
                    record_uuid=target_uuid,
                    action="quarantine_redact",
                ),
                *[
                    PrivacyRecord(
                        storage_type="sqlite",
                        record_type="memory_version",
                        record_uuid=row["memory_version_uuid"],
                        action="redact",
                    )
                    for row in self.connection.execute(
                        "SELECT memory_version_uuid FROM memory_versions WHERE memory_uuid = ?",
                        (target_uuid,),
                    )
                ],
            ]
        if target_type == "message" and target_uuid:
            return [
                PrivacyRecord(
                    storage_type="sqlite",
                    record_type="message",
                    record_uuid=target_uuid,
                    action="redact",
                ),
                *[
                    PrivacyRecord(
                        storage_type="sqlite",
                        record_type="text_unit",
                        record_uuid=row["text_unit_uuid"],
                        action="redact",
                    )
                    for row in self.connection.execute(
                        "SELECT text_unit_uuid FROM text_units WHERE message_uuid = ?",
                        (target_uuid,),
                    )
                ],
            ]
        if target_type == "session" and target_uuid:
            records: list[PrivacyRecord] = []
            for row in self.connection.execute(
                "SELECT message_uuid FROM messages WHERE session_uuid = ?",
                (target_uuid,),
            ):
                records.extend(
                    SQLiteCanonicalAdapter(self.connection).discover(
                        {"target_type": "message", "target_uuid": row["message_uuid"]}
                    )
                )
            records.append(
                PrivacyRecord(
                    storage_type="sqlite",
                    record_type="session",
                    record_uuid=target_uuid,
                    action="soft_delete",
                )
            )
            return records
        return []

    def quarantine(self, records: list[PrivacyRecord]) -> None:
        with self.connection:
            for record in records:
                if record.record_type == "memory" and record.record_uuid:
                    self.connection.execute(
                        """
                        UPDATE memories
                        SET status = 'forgotten', updated_at = ?
                        WHERE memory_uuid = ?
                        """,
                        (utc_now(), record.record_uuid),
                    )

    def erase(self, records: list[PrivacyRecord], request_uuid: str) -> ErasureResult:
        counts: Counter[str] = Counter()
        retained: Counter[str] = Counter()
        with self.connection:
            for record in records:
                if record.record_type == "memory" and record.record_uuid:
                    self.connection.execute(
                        """
                        UPDATE memories
                        SET status = 'forgotten', updated_at = ?
                        WHERE memory_uuid = ?
                        """,
                        (utc_now(), record.record_uuid),
                    )
                    counts["memory"] += 1
                elif record.record_type == "memory_version" and record.record_uuid:
                    sensitive_fragments = _sensitive_fragments_for_memory_version(
                        self.connection,
                        record.record_uuid,
                    )
                    self.connection.execute(
                        """
                        UPDATE memory_versions
                        SET normalized_text = '[erased]',
                            value_ijson = NULL,
                            change_reason_ijson = ?
                        WHERE memory_version_uuid = ?
                        """,
                        (
                            dump_ijson(
                                {
                                    "privacy_request_uuid": request_uuid,
                                    "redacted": True,
                                }
                            ),
                            record.record_uuid,
                        ),
                    )
                    counts.update(
                        _erase_memory_version_source_artifacts(
                            self.connection,
                            record.record_uuid,
                            request_uuid,
                        )
                    )
                    counts.update(
                        _erase_import_residue(
                            self.connection,
                            sensitive_fragments,
                            request_uuid,
                        )
                    )
                    counts["memory_version"] += 1
                elif record.record_type == "message" and record.record_uuid:
                    retained.update(
                        _retained_semantic_audit_counts(self.connection, record.record_uuid)
                    )
                    self.connection.execute(
                        """
                        UPDATE messages
                        SET raw_text = NULL,
                            raw_payload_ijson = NULL,
                            snapshot_ijson = NULL,
                            visibility = 'deleted',
                            is_deleted = 1,
                            redaction_status = 'erased',
                            updated_at = ?
                        WHERE message_uuid = ?
                        """,
                        (utc_now(), record.record_uuid),
                    )
                    self.connection.execute(
                        """
                        UPDATE candidate_evidence
                        SET evidence_text = '[erased]'
                        WHERE message_uuid = ?
                        """,
                        (record.record_uuid,),
                    )
                    counts.update(
                        _erase_jakobson_for_message(
                            self.connection,
                            record.record_uuid,
                            request_uuid,
                        )
                    )
                    counts["message"] += 1
                elif record.record_type == "text_unit" and record.record_uuid:
                    self.connection.execute(
                        """
                        UPDATE text_units
                        SET text = '[erased]', content_hash = ?
                        WHERE text_unit_uuid = ?
                        """,
                        (_fingerprint(record.record_uuid), record.record_uuid),
                    )
                    counts.update(
                        _erase_jakobson_for_units(
                            self.connection,
                            {record.record_uuid},
                            request_uuid,
                        )
                    )
                    counts["text_unit"] += 1
                elif record.record_type == "session" and record.record_uuid:
                    self.connection.execute(
                        """
                        UPDATE sessions
                        SET status = 'deleted', updated_at = ?
                        WHERE session_uuid = ?
                        """,
                        (utc_now(), record.record_uuid),
                    )
                    counts["session"] += 1
        return ErasureResult(
            erased_counts=dict(counts),
            retained_counts=dict(retained),
            exceptions=[],
        )

    def verify(self, records: list[PrivacyRecord]) -> VerificationResult:
        checks: dict[str, object] = {}
        for record in records:
            if record.record_type == "memory" and record.record_uuid:
                row = self.connection.execute(
                    "SELECT status FROM memories WHERE memory_uuid = ?",
                    (record.record_uuid,),
                ).fetchone()
                checks[f"memory:{record.record_uuid}"] = row is None or row["status"] == "forgotten"
            if record.record_type == "message" and record.record_uuid:
                row = self.connection.execute(
                    "SELECT raw_text, redaction_status FROM messages WHERE message_uuid = ?",
                    (record.record_uuid,),
                ).fetchone()
                checks[f"message:{record.record_uuid}"] = (
                    row is None or row["raw_text"] is None and row["redaction_status"] == "erased"
                )
        return VerificationResult(
            passed=all(bool(value) for value in checks.values()), checks=checks
        )


class SQLiteFTSAdapter:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def discover(self, request: dict[str, Any]) -> list[PrivacyRecord]:
        if request["target_type"] == "memory" and request["target_uuid"]:
            return [
                PrivacyRecord(
                    storage_type="sqlite_fts",
                    record_type="memory_version_fts",
                    record_uuid=request["target_uuid"],
                    action="delete",
                )
            ]
        return []

    def quarantine(self, records: list[PrivacyRecord]) -> None:
        self.erase(records, "quarantine")

    def erase(self, records: list[PrivacyRecord], request_uuid: str) -> ErasureResult:
        count = 0
        with self.connection:
            for record in records:
                if record.record_uuid:
                    self.connection.execute(
                        "DELETE FROM memory_version_fts WHERE memory_uuid = ?",
                        (record.record_uuid,),
                    )
                    count += 1
        return ErasureResult(
            erased_counts={"memory_version_fts": count}, retained_counts={}, exceptions=[]
        )

    def verify(self, records: list[PrivacyRecord]) -> VerificationResult:
        checks: dict[str, object] = {}
        for record in records:
            if record.record_uuid:
                count = self.connection.execute(
                    "SELECT COUNT(*) FROM memory_version_fts WHERE memory_uuid = ?",
                    (record.record_uuid,),
                ).fetchone()[0]
                checks[f"fts:{record.record_uuid}"] = int(count) == 0
        return VerificationResult(
            passed=all(bool(value) for value in checks.values()), checks=checks
        )


class EmbeddingAdapter:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def discover(self, request: dict[str, Any]) -> list[PrivacyRecord]:
        if request["target_type"] != "memory" or not request["target_uuid"]:
            return []
        return [
            PrivacyRecord(
                storage_type="sqlite",
                record_type="memory_version_embedding",
                record_uuid=row["memory_version_uuid"],
                action="delete",
            )
            for row in self.connection.execute(
                "SELECT memory_version_uuid FROM memory_versions WHERE memory_uuid = ?",
                (request["target_uuid"],),
            )
        ]

    def quarantine(self, records: list[PrivacyRecord]) -> None:
        return None

    def erase(self, records: list[PrivacyRecord], request_uuid: str) -> ErasureResult:
        count = 0
        with self.connection:
            for record in records:
                if record.record_uuid:
                    self.connection.execute(
                        "DELETE FROM memory_version_embeddings WHERE memory_version_uuid = ?",
                        (record.record_uuid,),
                    )
                    count += 1
        return ErasureResult(
            erased_counts={"memory_version_embedding": count},
            retained_counts={},
            exceptions=[],
        )

    def verify(self, records: list[PrivacyRecord]) -> VerificationResult:
        checks: dict[str, object] = {}
        for record in records:
            if record.record_uuid:
                count = self.connection.execute(
                    "SELECT COUNT(*) FROM memory_version_embeddings WHERE memory_version_uuid = ?",
                    (record.record_uuid,),
                ).fetchone()[0]
                checks[f"embedding:{record.record_uuid}"] = int(count) == 0
        return VerificationResult(
            passed=all(bool(value) for value in checks.values()), checks=checks
        )


class AttachmentAdapter:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def discover(self, request: dict[str, Any]) -> list[PrivacyRecord]:
        if request["target_type"] != "memory" or not request["target_uuid"]:
            return []
        return [
            PrivacyRecord(
                storage_type="sqlite",
                record_type="memory_context_attachment",
                record_uuid=row["attachment_uuid"],
                action="redact",
            )
            for row in self.connection.execute(
                """
                SELECT attachment_uuid
                FROM memory_context_attachments
                WHERE source_memory_uuids_ijson LIKE ?
                """,
                (f"%{request['target_uuid']}%",),
            )
        ]

    def quarantine(self, records: list[PrivacyRecord]) -> None:
        with self.connection:
            for record in records:
                if record.record_uuid:
                    self.connection.execute(
                        """
                        UPDATE memory_context_attachments
                        SET status = 'stale', stale_reason = 'privacy_quarantine'
                        WHERE attachment_uuid = ?
                        """,
                        (record.record_uuid,),
                    )

    def erase(self, records: list[PrivacyRecord], request_uuid: str) -> ErasureResult:
        count = 0
        with self.connection:
            for record in records:
                if record.record_uuid:
                    self.connection.execute(
                        """
                        UPDATE memory_context_attachments
                        SET rendered_attachment = NULL,
                            ijson_attachment = ?,
                            source_memory_uuids_ijson = NULL,
                            status = 'redacted',
                            stale_reason = 'privacy_erasure'
                        WHERE attachment_uuid = ?
                        """,
                        (
                            dump_ijson(
                                {
                                    "attachment_uuid": record.record_uuid,
                                    "redacted": True,
                                    "privacy_request_uuid": request_uuid,
                                }
                            ),
                            record.record_uuid,
                        ),
                    )
                    count += 1
        return ErasureResult(
            erased_counts={"memory_context_attachment": count}, retained_counts={}, exceptions=[]
        )

    def verify(self, records: list[PrivacyRecord]) -> VerificationResult:
        checks = {}
        for record in records:
            if record.record_uuid:
                row = self.connection.execute(
                    """
                    SELECT status, rendered_attachment
                    FROM memory_context_attachments
                    WHERE attachment_uuid = ?
                    """,
                    (record.record_uuid,),
                ).fetchone()
                checks[f"attachment:{record.record_uuid}"] = (
                    row is None
                    or row["status"] == "redacted"
                    and row["rendered_attachment"] is None
                )
        return VerificationResult(
            passed=all(bool(value) for value in checks.values()), checks=checks
        )


class MemoryBlockAdapter:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def discover(self, request: dict[str, Any]) -> list[PrivacyRecord]:
        if request["target_type"] != "memory" or not request["target_uuid"]:
            return []
        return [
            PrivacyRecord(
                storage_type="sqlite",
                record_type="memory_block",
                record_uuid=row["block_uuid"],
                action="invalidate",
            )
            for row in self.connection.execute(
                """
                SELECT DISTINCT mb.block_uuid
                FROM memory_block_sources mbs
                JOIN memory_block_versions mbv ON mbv.block_version_uuid = mbs.block_version_uuid
                JOIN memory_blocks mb ON mb.block_uuid = mbv.block_uuid
                WHERE mbs.memory_uuid = ?
                """,
                (request["target_uuid"],),
            )
        ]

    def quarantine(self, records: list[PrivacyRecord]) -> None:
        self.erase(records, "quarantine")

    def erase(self, records: list[PrivacyRecord], request_uuid: str) -> ErasureResult:
        counts: Counter[str] = Counter()
        with self.connection:
            for record in records:
                if record.record_uuid:
                    self.connection.execute(
                        """
                        UPDATE memory_blocks
                        SET value = '[redacted]',
                            source_memory_uuids_ijson = NULL,
                            build_status = 'stale',
                            status = 'redacted',
                            updated_at = ?
                        WHERE block_uuid = ?
                        """,
                        (utc_now(), record.record_uuid),
                    )
                    counts["memory_block"] += 1
                    cursor = self.connection.execute(
                        """
                        UPDATE memory_block_versions
                        SET value = '[redacted]',
                            structured_value_ijson = ?,
                            char_count = 10,
                            token_count = 1,
                            change_reason = 'privacy_erasure'
                        WHERE block_uuid = ?
                        """,
                        (
                            dump_ijson(
                                {
                                    "redacted": True,
                                    "privacy_request_uuid": request_uuid,
                                }
                            ),
                            record.record_uuid,
                        ),
                    )
                    counts["memory_block_version"] += cursor.rowcount
                    cursor = self.connection.execute(
                        """
                        DELETE FROM memory_block_sources
                        WHERE block_version_uuid IN (
                            SELECT block_version_uuid
                            FROM memory_block_versions
                            WHERE block_uuid = ?
                        )
                        """,
                        (record.record_uuid,),
                    )
                    counts["memory_block_source"] += cursor.rowcount
        return ErasureResult(erased_counts=dict(counts), retained_counts={}, exceptions=[])

    def verify(self, records: list[PrivacyRecord]) -> VerificationResult:
        checks = {}
        for record in records:
            if record.record_uuid:
                row = self.connection.execute(
                    "SELECT build_status FROM memory_blocks WHERE block_uuid = ?",
                    (record.record_uuid,),
                ).fetchone()
                checks[f"block:{record.record_uuid}"] = (
                    row is None or row["build_status"] == "stale"
                )
        return VerificationResult(
            passed=all(bool(value) for value in checks.values()), checks=checks
        )


class HotCacheAdapter(MemoryBlockAdapter):
    def discover(self, request: dict[str, Any]) -> list[PrivacyRecord]:
        if request["target_type"] != "memory" or not request["target_uuid"]:
            return []
        return [
            PrivacyRecord(
                storage_type="sqlite",
                record_type="session_hot_cache",
                record_uuid=row["session_uuid"],
                action="cleanup",
            )
            for row in self.connection.execute(
                "SELECT session_uuid FROM session_hot_cache WHERE recent_memory_uuids_ijson LIKE ?",
                (f"%{request['target_uuid']}%",),
            )
        ]

    def erase(self, records: list[PrivacyRecord], request_uuid: str) -> ErasureResult:
        count = 0
        with self.connection:
            for record in records:
                if record.record_uuid:
                    self.connection.execute(
                        """
                        UPDATE session_hot_cache
                        SET current_problem = '[redacted]',
                            last_user_intent = '[redacted]',
                            active_constraints_ijson = NULL,
                            unresolved_questions_ijson = NULL,
                            recent_entities_ijson = NULL,
                            recent_memory_uuids_ijson = NULL,
                            current_plan = '[redacted]',
                            last_updated_at = ?
                        WHERE session_uuid = ?
                        """,
                        (utc_now(), record.record_uuid),
                    )
                    count += 1
        return ErasureResult(
            erased_counts={"session_hot_cache": count}, retained_counts={}, exceptions=[]
        )

    def verify(self, records: list[PrivacyRecord]) -> VerificationResult:
        checks = {}
        for record in records:
            if record.record_uuid:
                row = self.connection.execute(
                    """
                    SELECT current_problem, recent_memory_uuids_ijson
                    FROM session_hot_cache
                    WHERE session_uuid = ?
                    """,
                    (record.record_uuid,),
                ).fetchone()
                checks[f"hot_cache:{record.record_uuid}"] = (
                    row is None
                    or row["current_problem"] == "[redacted]"
                    and row["recent_memory_uuids_ijson"] is None
                )
        return VerificationResult(
            passed=all(bool(value) for value in checks.values()), checks=checks
        )


class FalkorDBAdapter(MemoryBlockAdapter):
    def discover(self, request: dict[str, Any]) -> list[PrivacyRecord]:
        if request["target_type"] == "memory" and request["target_uuid"]:
            return [
                PrivacyRecord(
                    storage_type="falkordb",
                    record_type="memory_graph_node",
                    record_uuid=request["target_uuid"],
                    action="delete_if_enabled",
                )
            ]
        return []

    def erase(self, records: list[PrivacyRecord], request_uuid: str) -> ErasureResult:
        return ErasureResult(
            erased_counts={},
            retained_counts={"falkordb_not_enabled": len(records)},
            exceptions=[],
        )

    def verify(self, records: list[PrivacyRecord]) -> VerificationResult:
        return VerificationResult(passed=True, checks={"falkordb": "disabled_or_external"})


class ObjectStoreAdapter(FalkorDBAdapter):
    def discover(self, request: dict[str, Any]) -> list[PrivacyRecord]:
        return []


class PrivacyService:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self.repository = GovernanceRepository(connection)
        self.adapters: list[PrivacyStorageAdapter] = [
            SQLiteCanonicalAdapter(connection),
            SQLiteFTSAdapter(connection),
            EmbeddingAdapter(connection),
            MemoryBlockAdapter(connection),
            AttachmentAdapter(connection),
            HotCacheAdapter(connection),
            FalkorDBAdapter(connection),
            ObjectStoreAdapter(connection),
        ]

    def preview_request(
        self,
        request_type: str,
        target_type: str,
        requested_scope: Any,
        actor_type: str,
        target_uuid: str | None = None,
        actor_uuid: str | None = None,
    ) -> dict[str, Any]:
        if target_type in {"project", "workspace"} and actor_type not in {"admin", "system"}:
            raise RepositoryError("actor is not authorized for cross-scope erasure")
        request_uuid = new_uuid()
        token = f"confirm-{request_uuid}"
        request = {
            "privacy_request_uuid": request_uuid,
            "request_type": request_type,
            "target_type": target_type,
            "target_uuid": target_uuid,
        }
        records = self._discover_records(request)
        stored = self.repository.create_privacy_request(
            request_uuid=request_uuid,
            request_type=request_type,
            target_type=target_type,
            target_uuid=target_uuid,
            requested_scope=requested_scope,
            actor_type=actor_type,
            actor_uuid=actor_uuid,
            policy_decision={
                "decision": "technical_preview_only",
                "legal_conclusion": None,
                "irreversible_impact_estimate": len(records),
            },
            confirmation_token_hash=hash_token(token),
            confirmation_expires_at=(datetime.now(UTC).replace(microsecond=0) + timedelta(hours=1))
            .isoformat()
            .replace("+00:00", "Z"),
        )
        for record in records:
            self.repository.add_privacy_item(
                request_uuid,
                record.storage_type,
                record.record_type,
                record.action,
                record.record_uuid,
            )
        return {
            **stored,
            "confirmation_token": token,
            "items": [record.model_dump(mode="json") for record in records],
        }

    def confirm_request(self, request_uuid: str, confirmation_token: str) -> dict[str, Any]:
        request = self.repository.get_privacy_request(request_uuid)
        if request["confirmation_token_hash"] != hash_token(confirmation_token):
            raise RepositoryError("invalid confirmation token")
        expires_at = datetime.fromisoformat(
            request["confirmation_expires_at"].replace("Z", "+00:00")
        )
        if expires_at < datetime.now(UTC):
            raise RepositoryError("confirmation token expired")
        return self.repository.update_privacy_request(
            request_uuid,
            {"status": "confirmed", "confirmed_at": utc_now()},
        )

    def execute_request(self, request_uuid: str) -> dict[str, Any]:
        existing_receipt = self.repository.get_receipt(request_uuid)
        if existing_receipt is not None:
            return existing_receipt

        request = self.repository.get_privacy_request(request_uuid)
        if request["status"] not in {"confirmed", "executing", "partial_failure"}:
            raise RepositoryError("privacy request must be confirmed before execution")
        self.connection.execute("PRAGMA secure_delete=ON")
        self.repository.update_privacy_request(request_uuid, {"status": "executing"})
        records = self._records_from_items(self.repository.list_privacy_items(request_uuid))

        for adapter in self.adapters:
            adapter_records = [record for record in records if _adapter_matches(adapter, record)]
            adapter.quarantine(adapter_records)

        erased_counts: Counter[str] = Counter()
        retained_counts: Counter[str] = Counter()
        exceptions: list[dict[str, object]] = []
        verification: dict[str, object] = {}
        for adapter in self.adapters:
            adapter_records = [record for record in records if _adapter_matches(adapter, record)]
            try:
                result = adapter.erase(adapter_records, request_uuid)
                erased_counts.update(result.erased_counts)
                retained_counts.update(result.retained_counts)
                exceptions.extend(result.exceptions)
                verify = adapter.verify(adapter_records)
                verification[type(adapter).__name__] = verify.model_dump(mode="json")
            except Exception as error:
                exceptions.append(
                    {
                        "adapter": type(adapter).__name__,
                        "error_type": type(error).__name__,
                    }
                )
        self.connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        request_fingerprint = canonical_hash_ijson(
            {
                "privacy_request_uuid": request_uuid,
                "target_type": request["target_type"],
                "target_uuid_hash": _fingerprint(str(request["target_uuid"])),
            }
        )
        receipt = self.repository.create_receipt(
            privacy_request_uuid=request_uuid,
            request_fingerprint=request_fingerprint,
            erased_counts=dict(erased_counts),
            retained_counts=dict(retained_counts),
            exceptions=exceptions,
            verification=verification,
        )
        self.repository.update_privacy_request(
            request_uuid,
            {
                "status": "completed" if not exceptions else "partial_failure",
                "completed_at": utc_now() if not exceptions else None,
            },
        )
        return receipt

    def retry_request(self, request_uuid: str) -> dict[str, Any]:
        self.repository.update_privacy_request(request_uuid, {"status": "partial_failure"})
        return self.execute_request(request_uuid)

    def _discover_records(self, request: dict[str, Any]) -> list[PrivacyRecord]:
        records: list[PrivacyRecord] = []
        seen: set[tuple[str, str, str | None]] = set()
        for adapter in self.adapters:
            for record in adapter.discover(request):
                key = (record.storage_type, record.record_type, record.record_uuid)
                if key not in seen:
                    seen.add(key)
                    records.append(record)
        return records

    def _records_from_items(self, items: list[dict[str, Any]]) -> list[PrivacyRecord]:
        return [
            PrivacyRecord(
                storage_type=item["storage_type"],
                record_type=item["record_type"],
                record_uuid=item["record_uuid"],
                action=item["action"],
            )
            for item in items
        ]


def _adapter_matches(adapter: PrivacyStorageAdapter, record: PrivacyRecord) -> bool:
    name = type(adapter).__name__
    if name == "SQLiteCanonicalAdapter":
        return record.storage_type == "sqlite" and record.record_type in {
            "memory",
            "memory_version",
            "message",
            "text_unit",
            "session",
        }
    if name == "SQLiteFTSAdapter":
        return record.record_type == "memory_version_fts"
    if name == "EmbeddingAdapter":
        return record.record_type == "memory_version_embedding"
    if name == "MemoryBlockAdapter":
        return record.record_type == "memory_block"
    if name == "AttachmentAdapter":
        return record.record_type == "memory_context_attachment"
    if name == "HotCacheAdapter":
        return record.record_type == "session_hot_cache"
    if name == "FalkorDBAdapter":
        return record.storage_type == "falkordb"
    if name == "ObjectStoreAdapter":
        return record.storage_type == "object_store"
    return False


def _fingerprint(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _sensitive_fragments_for_memory_version(
    connection: sqlite3.Connection,
    memory_version_uuid: str,
) -> list[str]:
    row = connection.execute(
        """
        SELECT normalized_text, value_ijson, change_reason_ijson
        FROM memory_versions
        WHERE memory_version_uuid = ?
        """,
        (memory_version_uuid,),
    ).fetchone()
    if row is None:
        return []
    fragments = [row["normalized_text"]]
    for column in ("value_ijson", "change_reason_ijson"):
        if row[column]:
            fragments.append(str(row[column]))
            try:
                fragments.extend(_string_fragments(json.loads(row[column])))
            except json.JSONDecodeError:
                continue
    return _dedupe_sensitive_fragments(fragments)


def _erase_memory_version_source_artifacts(
    connection: sqlite3.Connection,
    memory_version_uuid: str,
    request_uuid: str,
) -> Counter[str]:
    counts: Counter[str] = Counter()
    linked_rows = connection.execute(
        """
        SELECT candidate_uuid, evidence_uuid
        FROM memory_evidence_links
        WHERE memory_version_uuid = ?
        """,
        (memory_version_uuid,),
    ).fetchall()
    candidate_uuids = {row["candidate_uuid"] for row in linked_rows}
    evidence_uuids = {row["evidence_uuid"] for row in linked_rows}
    if candidate_uuids:
        placeholders = ",".join("?" for _ in candidate_uuids)
        cursor = connection.execute(
            f"""
            UPDATE memory_candidates
            SET object_ijson = NULL,
                normalized_text = '[erased]',
                extraction_metadata_ijson = ?,
                status = 'redacted'
            WHERE candidate_uuid IN ({placeholders})
            """,
            (
                dump_ijson(
                    {
                        "redacted": True,
                        "privacy_request_uuid": request_uuid,
                    }
                ),
                *candidate_uuids,
            ),
        )
        counts["memory_candidate"] += cursor.rowcount
    if evidence_uuids:
        placeholders = ",".join("?" for _ in evidence_uuids)
        evidence_rows = connection.execute(
            f"""
            SELECT text_unit_uuid
            FROM candidate_evidence
            WHERE evidence_uuid IN ({placeholders})
            """,
            tuple(evidence_uuids),
        ).fetchall()
        text_unit_uuids = {row["text_unit_uuid"] for row in evidence_rows}
        cursor = connection.execute(
            f"""
            UPDATE candidate_evidence
            SET evidence_text = '[erased]'
            WHERE evidence_uuid IN ({placeholders})
            """,
            tuple(evidence_uuids),
        )
        counts["candidate_evidence"] += cursor.rowcount
        if text_unit_uuids:
            text_placeholders = ",".join("?" for _ in text_unit_uuids)
            cursor = connection.execute(
                f"""
                UPDATE text_units
                SET text = '[erased]', content_hash = ?
                WHERE text_unit_uuid IN ({text_placeholders})
                """,
                (_fingerprint(request_uuid), *text_unit_uuids),
            )
            counts["text_unit"] += cursor.rowcount
            counts.update(_erase_jakobson_for_units(connection, text_unit_uuids, request_uuid))
    return counts


def _erase_jakobson_for_message(
    connection: sqlite3.Connection,
    message_uuid: str,
    request_uuid: str,
) -> Counter[str]:
    rows = connection.execute(
        "SELECT text_unit_uuid FROM text_units WHERE message_uuid = ?",
        (message_uuid,),
    ).fetchall()
    counts = _erase_jakobson_for_units(
        connection,
        {row["text_unit_uuid"] for row in rows},
        request_uuid,
    )
    redacted_payload = dump_ijson({"redacted": True, "privacy_request_uuid": request_uuid})
    cursor = connection.execute(
        """
        UPDATE jakobson_analysis_runs
        SET raw_output_ijson = ?,
            warnings_ijson = ?
        WHERE message_uuid = ?
        """,
        (redacted_payload, redacted_payload, message_uuid),
    )
    counts["jakobson_analysis_run"] += cursor.rowcount
    return counts


def _retained_semantic_audit_counts(
    connection: sqlite3.Connection, message_uuid: str
) -> Counter[str]:
    counts: Counter[str] = Counter()
    if not _table_exists(connection, "semantic_coverage_runs"):
        return counts
    run_count = int(
        connection.execute(
            "SELECT COUNT(*) FROM semantic_coverage_runs WHERE message_uuid = ?",
            (message_uuid,),
        ).fetchone()[0]
    )
    item_count = int(
        connection.execute(
            """
            SELECT COUNT(*)
            FROM semantic_coverage_items AS item
            JOIN semantic_coverage_runs AS run
              ON run.coverage_run_uuid = item.coverage_run_uuid
            WHERE run.message_uuid = ?
            """,
            (message_uuid,),
        ).fetchone()[0]
    )
    link_count = int(
        connection.execute(
            """
            SELECT COUNT(*)
            FROM semantic_candidate_links AS link
            JOIN semantic_coverage_items AS item
              ON item.coverage_item_uuid = link.coverage_item_uuid
            JOIN semantic_coverage_runs AS run
              ON run.coverage_run_uuid = item.coverage_run_uuid
            WHERE run.message_uuid = ?
            """,
            (message_uuid,),
        ).fetchone()[0]
    )
    if run_count:
        counts["semantic_coverage_run_audit"] = run_count
    if item_count:
        counts["semantic_coverage_item_audit"] = item_count
    if link_count:
        counts["semantic_candidate_link_audit"] = link_count
    return counts


def _erase_jakobson_for_units(
    connection: sqlite3.Connection,
    text_unit_uuids: set[str],
    request_uuid: str,
) -> Counter[str]:
    counts: Counter[str] = Counter()
    if not text_unit_uuids or not _table_exists(connection, "jakobson_sentence_annotations"):
        return counts
    placeholders = ",".join("?" for _ in text_unit_uuids)
    redacted_payload = dump_ijson({"redacted": True, "privacy_request_uuid": request_uuid})
    run_rows = connection.execute(
        f"""
        SELECT DISTINCT analysis_run_uuid
        FROM jakobson_sentence_annotations
        WHERE unit_uuid IN ({placeholders})
        """,
        tuple(text_unit_uuids),
    ).fetchall()
    cursor = connection.execute(
        f"""
        UPDATE jakobson_sentence_annotations
        SET sentence_text = '[erased]',
            sentence_hash = ?,
            sender_evidence = NULL,
            receiver_evidence = NULL,
            message_evidence = NULL,
            context_evidence = NULL,
            code_evidence = NULL,
            contact_channel_evidence = NULL,
            raw_sentence_output_ijson = ?
        WHERE unit_uuid IN ({placeholders})
        """,
        (_fingerprint(request_uuid), redacted_payload, *text_unit_uuids),
    )
    counts["jakobson_sentence_annotation"] += cursor.rowcount
    run_uuids = {row["analysis_run_uuid"] for row in run_rows}
    if run_uuids:
        run_placeholders = ",".join("?" for _ in run_uuids)
        cursor = connection.execute(
            f"""
            UPDATE jakobson_analysis_runs
            SET raw_output_ijson = ?,
                warnings_ijson = ?
            WHERE analysis_run_uuid IN ({run_placeholders})
            """,
            (redacted_payload, redacted_payload, *run_uuids),
        )
        counts["jakobson_analysis_run"] += cursor.rowcount
    return counts


def _erase_import_residue(
    connection: sqlite3.Connection,
    fragments: list[str],
    request_uuid: str,
) -> Counter[str]:
    counts: Counter[str] = Counter()
    redacted_payload = dump_ijson(
        {
            "redacted": True,
            "privacy_request_uuid": request_uuid,
        }
    )
    for fragment in fragments:
        like_fragment = f"%{fragment}%"
        for column in ("raw_payload_ijson", "normalized_payload_ijson"):
            cursor = connection.execute(
                f"""
                UPDATE import_records
                SET {column} = ?,
                    status = 'redacted'
                WHERE {column} LIKE ?
                """,
                (redacted_payload, like_fragment),
            )
            counts["import_record"] += cursor.rowcount
        cursor = connection.execute(
            """
            UPDATE import_mappings
            SET source_object_id = '[erased]',
                source_fingerprint = ?
            WHERE source_object_id LIKE ?
               OR source_fingerprint LIKE ?
            """,
            (_fingerprint(f"{request_uuid}:{fragment}"), like_fragment, like_fragment),
        )
        counts["import_mapping"] += cursor.rowcount
    return counts


def _string_fragments(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [fragment for item in value for fragment in _string_fragments(item)]
    if isinstance(value, dict):
        return [fragment for item in value.values() for fragment in _string_fragments(item)]
    return []


def _dedupe_sensitive_fragments(fragments: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for fragment in fragments:
        fragment = fragment.strip()
        if len(fragment) < 8 or fragment in seen:
            continue
        seen.add(fragment)
        deduped.append(fragment)
    return deduped


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    row = connection.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?",
        (table,),
    ).fetchone()
    return row is not None
