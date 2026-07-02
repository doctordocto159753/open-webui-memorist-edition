from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from memcore.models import new_uuid
from memcore.validators.ijson import dump_ijson, load_ijson

CLAIM_NEXT_JOB_SQL = """
UPDATE jobs
SET status = 'running',
    locked_by = %s,
    locked_at = now(),
    attempts = attempts + 1,
    updated_at = now()
WHERE job_uuid = (
  SELECT job_uuid
  FROM jobs
  WHERE status = 'pending'
    AND run_after <= now()
  ORDER BY priority DESC, run_after ASC, created_at ASC
  FOR UPDATE SKIP LOCKED
  LIMIT 1
)
RETURNING *
"""

RECOVER_STALE_JOBS_SQL = """
UPDATE jobs
SET status = CASE
        WHEN attempts >= max_attempts THEN 'dead'
        ELSE 'pending'
    END,
    locked_by = NULL,
    locked_at = NULL,
    last_error_sanitized = COALESCE(last_error_sanitized, 'lease expired'),
    updated_at = now()
WHERE status = 'running'
  AND locked_at < %s
RETURNING *
"""


class PostgresJobRepository:
    def __init__(self, connection: Any) -> None:
        self.connection = connection

    def enqueue_job(
        self,
        job_type: str,
        payload: Any,
        *,
        priority: int = 50,
        idempotency_key: str | None = None,
        run_after: datetime | None = None,
        max_attempts: int = 3,
    ) -> dict[str, Any]:
        payload_json = load_ijson(dump_ijson(payload))
        job_uuid = new_uuid()
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO jobs (
                    job_uuid, job_type, priority, status, payload_jsonb,
                    idempotency_key, run_after, attempts, max_attempts
                )
                VALUES (%s, %s, %s, 'pending', %s::jsonb, %s, %s, 0, %s)
                ON CONFLICT (job_type, idempotency_key) DO UPDATE
                SET updated_at = jobs.updated_at
                RETURNING *
                """,
                (
                    job_uuid,
                    job_type,
                    priority,
                    json.dumps(payload_json, separators=(",", ":")),
                    idempotency_key,
                    run_after or datetime.now(UTC),
                    max_attempts,
                ),
            )
            row = _row_to_dict(cursor)
        self.connection.commit()
        return row

    def claim_next_job(self, worker_id: str) -> dict[str, Any] | None:
        with self.connection.cursor() as cursor:
            cursor.execute(CLAIM_NEXT_JOB_SQL, (worker_id,))
            row = _optional_row_to_dict(cursor)
        self.connection.commit()
        return row

    def recover_stale_jobs(self, lease_timeout_seconds: int = 300) -> list[dict[str, Any]]:
        cutoff = datetime.now(UTC) - timedelta(seconds=lease_timeout_seconds)
        with self.connection.cursor() as cursor:
            cursor.execute(RECOVER_STALE_JOBS_SQL, (cutoff,))
            rows = _rows_to_dicts(cursor)
        self.connection.commit()
        return rows


def _optional_row_to_dict(cursor: Any) -> dict[str, Any] | None:
    row = cursor.fetchone()
    if row is None:
        return None
    return _coerce_row(cursor, row)


def _row_to_dict(cursor: Any) -> dict[str, Any]:
    row = _optional_row_to_dict(cursor)
    if row is None:
        raise RuntimeError("PostgreSQL statement returned no row")
    return row


def _rows_to_dicts(cursor: Any) -> list[dict[str, Any]]:
    return [_coerce_row(cursor, row) for row in cursor.fetchall()]


def _coerce_row(cursor: Any, row: Any) -> dict[str, Any]:
    if isinstance(row, dict):
        return row
    if hasattr(row, "keys"):
        return dict(row)
    description = getattr(cursor, "description", None)
    if description is None:
        return {"row": row}
    columns = [column[0] for column in description]
    return dict(zip(columns, row, strict=False))
