from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from memcore.imports.service import ImportService
from memcore.storage.commands import WriteResult
from memcore.storage.write_actor import get_write_actor


@dataclass(frozen=True)
class ImportBatchCommitCommand:
    import_run_uuid: str
    object_store_path: str
    processing_mode: str = "none"
    batch_size: int = 100
    max_write_queue_depth: int = 500
    batch_index: int = 0
    command_type: str = "import.batch_commit"
    priority: int = 30
    max_transaction_ms: int = 30_000
    can_batch: bool = False

    @property
    def idempotency_key(self) -> str | None:
        return None

    @property
    def estimated_write_weight(self) -> int:
        return max(1, self.batch_size * 12)

    def execute(self, connection: sqlite3.Connection) -> WriteResult:
        result = ImportService(connection, self.object_store_path).commit_next_batch(
            self.import_run_uuid,
            processing_mode=self.processing_mode,
            batch_size=self.batch_size,
            max_write_queue_depth=self.max_write_queue_depth,
        )
        return WriteResult(
            command_type=self.command_type,
            target_type="import_run",
            target_uuid=self.import_run_uuid,
            result={
                "batch_index": self.batch_index,
                "status": result["status"],
                "imported_conversations": result.get("imported_conversations", 0),
                "imported_messages": result.get("imported_messages", 0),
                "skipped_records": result.get("skipped_records", 0),
                "error_count": result.get("error_count", 0),
            },
        )


@dataclass(frozen=True)
class ImportProcessingBatchCommand:
    import_run_uuid: str
    object_store_path: str
    batch_size: int = 25
    command_type: str = "import.processing_batch"
    priority: int = 30
    max_transaction_ms: int = 120_000
    can_batch: bool = False

    @property
    def idempotency_key(self) -> str | None:
        return None

    @property
    def estimated_write_weight(self) -> int:
        return max(1, self.batch_size * 20)

    def execute(self, connection: sqlite3.Connection) -> WriteResult:
        result = ImportService(connection, self.object_store_path).process_next_batch(
            self.import_run_uuid, self.batch_size
        )
        return WriteResult(
            command_type=self.command_type,
            target_type="import_run",
            target_uuid=self.import_run_uuid,
            result=result,
        )


def commit_import_via_actor(
    db_path: str | Path,
    object_store_path: str,
    import_run_uuid: str,
    processing_mode: str = "none",
    batch_size: int = 100,
    max_write_queue_depth: int = 500,
    timeout_per_batch: float | None = 120,
    max_batches: int = 100_000,
) -> dict[str, Any]:
    actor = get_write_actor(db_path)
    last_result: dict[str, Any] | None = None
    for batch_index in range(max_batches):
        write_result = actor.submit_sync(
            ImportBatchCommitCommand(
                import_run_uuid=import_run_uuid,
                object_store_path=object_store_path,
                processing_mode=processing_mode,
                batch_size=batch_size,
                max_write_queue_depth=max_write_queue_depth,
                batch_index=batch_index,
            ),
            timeout=timeout_per_batch,
        )
        last_result = write_result.result
        if last_result["status"] != "committing":
            return last_result
    raise TimeoutError(f"Import commit exceeded maximum batch count: {max_batches}")


def process_import_batch_via_actor(
    db_path: str | Path,
    object_store_path: str,
    import_run_uuid: str,
    batch_size: int = 25,
    timeout: float | None = 300,
) -> dict[str, Any]:
    result = get_write_actor(db_path).submit_sync(
        ImportProcessingBatchCommand(
            import_run_uuid=import_run_uuid,
            object_store_path=object_store_path,
            batch_size=batch_size,
        ),
        timeout=timeout,
    )
    return result.result
