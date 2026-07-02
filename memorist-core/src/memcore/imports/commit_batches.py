from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from typing import Any

from memcore.models import new_uuid, utc_now
from memcore.validators.ijson import canonical_hash_ijson


class ImportCommitBatchRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def start_batch(
        self,
        import_run_uuid: str,
        records: Sequence[dict[str, Any]],
    ) -> dict[str, Any]:
        batch_number = self._next_batch_number(import_run_uuid)
        fingerprint = canonical_hash_ijson(
            {
                "import_run_uuid": import_run_uuid,
                "records": [
                    row.get("imported_conversation_uuid") or row.get("import_record_uuid")
                    for row in records
                ],
            }
        )
        existing = self.connection.execute(
            """
            SELECT *
            FROM import_commit_batches
            WHERE import_run_uuid = ?
              AND batch_fingerprint = ?
            """,
            (import_run_uuid, fingerprint),
        ).fetchone()
        if existing is not None:
            return dict(existing)
        row = {
            "batch_uuid": new_uuid(),
            "import_run_uuid": import_run_uuid,
            "batch_number": batch_number,
            "batch_fingerprint": fingerprint,
            "status": "running",
            "record_count": len(records),
            "started_at": utc_now(),
            "finished_at": None,
            "error_sanitized": None,
            "created_at": utc_now(),
            "schema_version": 1,
        }
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO import_commit_batches (
                    batch_uuid, import_run_uuid, batch_number, batch_fingerprint,
                    status, record_count, started_at, finished_at, error_sanitized,
                    created_at, schema_version
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                tuple(row.values()),
            )
        return row

    def finish_batch(
        self,
        batch_uuid: str,
        status: str = "committed",
        error_sanitized: str | None = None,
    ) -> dict[str, Any]:
        with self.connection:
            self.connection.execute(
                """
                UPDATE import_commit_batches
                SET status = ?, finished_at = ?, error_sanitized = ?
                WHERE batch_uuid = ?
                """,
                (status, utc_now(), error_sanitized, batch_uuid),
            )
        row = self.connection.execute(
            "SELECT * FROM import_commit_batches WHERE batch_uuid = ?",
            (batch_uuid,),
        ).fetchone()
        if row is None:
            raise RuntimeError(f"Import commit batch not found: {batch_uuid}")
        return dict(row)

    def list_batches(self, import_run_uuid: str) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in self.connection.execute(
                """
                SELECT *
                FROM import_commit_batches
                WHERE import_run_uuid = ?
                ORDER BY batch_number
                """,
                (import_run_uuid,),
            )
        ]

    def _next_batch_number(self, import_run_uuid: str) -> int:
        row = self.connection.execute(
            """
            SELECT COALESCE(MAX(batch_number), 0) + 1
            FROM import_commit_batches
            WHERE import_run_uuid = ?
            """,
            (import_run_uuid,),
        ).fetchone()
        return int(row[0])
