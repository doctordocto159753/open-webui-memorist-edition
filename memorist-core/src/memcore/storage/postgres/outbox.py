from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from memcore.models import new_uuid
from memcore.storage.postgres.jobs import _coerce_row, _row_to_dict, _rows_to_dicts
from memcore.validators.ijson import dump_ijson, load_ijson

OutboxTable = Literal[
    "graph_projection_outbox",
    "embedding_outbox",
    "memory_processing_outbox",
    "privacy_erasure_outbox",
]

OUTBOX_TABLES: set[str] = {
    "graph_projection_outbox",
    "embedding_outbox",
    "memory_processing_outbox",
    "privacy_erasure_outbox",
}


def claim_outbox_sql(table_name: OutboxTable) -> str:
    _validate_outbox_table(table_name)
    return f"""
    UPDATE {table_name}
    SET status = 'running',
        locked_by = %s,
        locked_at = now(),
        attempts = attempts + 1,
        updated_at = now()
    WHERE outbox_uuid = (
      SELECT outbox_uuid
      FROM {table_name}
      WHERE status IN ('pending', 'retry')
        AND run_after <= now()
      ORDER BY priority DESC, run_after ASC, created_at ASC
      FOR UPDATE SKIP LOCKED
      LIMIT 1
    )
    RETURNING *
    """


def recover_outbox_sql(table_name: OutboxTable) -> str:
    _validate_outbox_table(table_name)
    return f"""
    UPDATE {table_name}
    SET status = 'pending',
        locked_by = NULL,
        locked_at = NULL,
        last_error_sanitized = COALESCE(last_error_sanitized, 'lease expired'),
        updated_at = now()
    WHERE status = 'running'
      AND locked_at < %s
    RETURNING *
    """


class PostgresOutboxRepository:
    def __init__(self, connection: Any, table_name: OutboxTable) -> None:
        _validate_outbox_table(table_name)
        self.connection = connection
        self.table_name = table_name

    def enqueue(
        self,
        event_type: str,
        source_type: str,
        source_uuid: str,
        payload: Any,
        *,
        priority: int = 50,
        run_after: datetime | None = None,
    ) -> dict[str, Any]:
        payload_json = load_ijson(dump_ijson(payload))
        outbox_uuid = new_uuid()
        with self.connection.cursor() as cursor:
            cursor.execute(
                f"""
                INSERT INTO {self.table_name} (
                    outbox_uuid, event_type, source_type, source_uuid,
                    payload_jsonb, status, priority, attempts, run_after
                )
                VALUES (%s, %s, %s, %s, %s::jsonb, 'pending', %s, 0, %s)
                ON CONFLICT (event_type, source_type, source_uuid) DO UPDATE
                SET updated_at = {self.table_name}.updated_at
                RETURNING *
                """,
                (
                    outbox_uuid,
                    event_type,
                    source_type,
                    source_uuid,
                    json.dumps(payload_json, separators=(",", ":")),
                    priority,
                    run_after or datetime.now(UTC),
                ),
            )
            row = _row_to_dict(cursor)
        self.connection.commit()
        return row

    def claim_next(self, worker_id: str) -> dict[str, Any] | None:
        with self.connection.cursor() as cursor:
            cursor.execute(claim_outbox_sql(self.table_name), (worker_id,))
            row = cursor.fetchone()
            result = None if row is None else _coerce_row(cursor, row)
        self.connection.commit()
        return result

    def recover_stale(self, lease_timeout_seconds: int = 300) -> list[dict[str, Any]]:
        cutoff = datetime.now(UTC) - timedelta(seconds=lease_timeout_seconds)
        with self.connection.cursor() as cursor:
            cursor.execute(recover_outbox_sql(self.table_name), (cutoff,))
            rows = _rows_to_dicts(cursor)
        self.connection.commit()
        return rows


def _validate_outbox_table(table_name: str) -> None:
    if table_name not in OUTBOX_TABLES:
        raise ValueError(f"Unsupported outbox table: {table_name}")
