from __future__ import annotations

import json
import logging
import os
import socket
import threading
from typing import Any

from memcore.config import Settings
from memcore.imports.runtime import import_connection
from memcore.memory_worker.pipeline import MemoryWorkerPipeline
from memcore.memory_worker.postgres.pipeline import PostgresMemoryWorkerPipeline
from memcore.model_control.security import sanitize_error_message
from memcore.models import new_uuid, utc_now
from memcore.storage.sqlite import connect

LOGGER = logging.getLogger(__name__)
_POLL_SECONDS = 0.25


class MemoryJobWorkerService:
    """Process durable Open WebUI memory-extraction jobs outside chat requests."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.enabled = _worker_enabled(settings)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if not self.enabled:
            LOGGER.info("memory job worker disabled")
            return
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._recover_stale_jobs()
        self._thread = threading.Thread(
            target=self._run,
            name="memorist-memory-worker",
            daemon=True,
        )
        self._thread.start()
        LOGGER.info("started memory job worker")

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def process_once(self, worker_id: str | None = None) -> bool:
        if not self.enabled:
            return False
        owner = worker_id or _worker_id()
        if self.settings.runtime_profile == "lite":
            return self._process_once_sqlite(owner)
        with import_connection(self.settings) as connection:
            job = _claim_next_extraction_job(connection, owner)
            if job is None:
                return False
            attempt_uuid = _record_attempt(connection, job, owner)
            connection.commit()
            try:
                payload = _job_payload(job)
                message_uuid = str(payload["message_uuid"])
                pipeline = PostgresMemoryWorkerPipeline(connection, self.settings)
                prepared = pipeline.prepare_message(message_uuid)
                pipeline.process_message(
                    message_uuid,
                    job_uuid=str(job["job_uuid"]),
                    prepared_inference=prepared,
                )
                _record_success(connection, job, attempt_uuid, message_uuid, owner)
                connection.commit()
            except Exception as error:
                connection.rollback()
                _record_failure(connection, job, attempt_uuid, owner, error)
                connection.commit()
                LOGGER.exception("memory job %s failed", job["job_uuid"])
            return True

    def _process_once_sqlite(self, owner: str) -> bool:
        connection = connect(self.settings.db_path)
        try:
            job = _claim_next_extraction_job_sqlite(connection, owner)
            if job is None:
                return False
            try:
                payload = _job_payload(job)
                message_uuid = str(payload["message_uuid"])
                MemoryWorkerPipeline(connection, self.settings).process_message(
                    message_uuid,
                    job_uuid=str(job["job_uuid"]),
                    model_target=payload,
                )
                _record_success_sqlite(connection, job, message_uuid, owner)
            except Exception as error:
                connection.rollback()
                _record_failure_sqlite(connection, job, owner, error)
                LOGGER.exception("Lite memory job %s failed", job["job_uuid"])
            return True
        finally:
            connection.close()

    def _run(self) -> None:
        owner = _worker_id()
        while not self._stop.is_set():
            try:
                did_work = self.process_once(owner)
            except Exception:
                LOGGER.exception("memory job worker iteration failed")
                did_work = False
            if not did_work:
                self._stop.wait(_POLL_SECONDS)

    def _recover_stale_jobs(self) -> None:
        if self.settings.runtime_profile == "lite":
            connection = connect(self.settings.db_path)
            try:
                connection.execute(
                    """
                    UPDATE jobs
                    SET status = CASE WHEN attempts >= max_attempts THEN 'dead' ELSE 'pending' END,
                        locked_by = NULL, locked_at = NULL,
                        last_error_sanitized = COALESCE(last_error_sanitized, 'lease expired')
                    WHERE job_type = 'memory_extraction' AND status = 'running'
                    """
                )
                connection.commit()
            finally:
                connection.close()
            return
        with import_connection(self.settings) as connection:
            connection.execute(
                """
                UPDATE jobs
                SET status = CASE WHEN attempts >= max_attempts THEN 'dead' ELSE 'pending' END,
                    locked_by = NULL, locked_at = NULL,
                    last_error_sanitized = COALESCE(last_error_sanitized, 'lease expired'),
                    updated_at = now()
                WHERE job_type = 'memory_extraction' AND status = 'running'
                  AND locked_at < now() - interval '5 minutes'
                """
            )
            connection.commit()


def _worker_enabled(settings: Settings) -> bool:
    if not settings.enable_memory_worker:
        return False
    if settings.runtime_profile == "lite":
        return settings.canonical_store == "sqlite"
    return bool(
        settings.runtime_profile == "full"
        and settings.canonical_store == "postgres"
        and settings.postgres_dsn
    )


def _worker_id() -> str:
    return f"memory-worker:{socket.gethostname()}:{os.getpid()}"


def _claim_next_extraction_job(connection: Any, worker_id: str) -> dict[str, Any] | None:
    row = connection.execute(
        """
        UPDATE jobs
        SET status = 'running', locked_by = ?, locked_at = now(),
            attempts = attempts + 1, updated_at = now()
        WHERE job_uuid = (
          SELECT job_uuid FROM jobs
          WHERE status = 'pending' AND job_type = 'memory_extraction'
            AND run_after <= now()
          ORDER BY priority DESC, run_after ASC, created_at ASC
          FOR UPDATE SKIP LOCKED LIMIT 1
        )
        RETURNING *
        """,
        (worker_id,),
    ).fetchone()
    connection.commit()
    return dict(row) if row is not None else None


def _record_attempt(connection: Any, job: dict[str, Any], worker_id: str) -> str:
    attempt_uuid = new_uuid()
    connection.execute(
        """
        INSERT INTO job_attempts (
          job_attempt_uuid, job_uuid, worker_id, status, started_at, schema_version
        ) VALUES (?, ?, ?, 'running', ?, 1)
        """,
        (attempt_uuid, job["job_uuid"], worker_id, utc_now()),
    )
    return attempt_uuid


def _record_success(
    connection: Any,
    job: dict[str, Any],
    attempt_uuid: str,
    message_uuid: str,
    worker_id: str,
) -> None:
    finished = utc_now()
    connection.execute(
        """
        UPDATE jobs
        SET status = 'succeeded', locked_by = NULL, locked_at = NULL,
            last_error = NULL, last_error_sanitized = NULL, run_after = NULL,
            updated_at = ?
        WHERE job_uuid = ? AND locked_by = ?
        """,
        (finished, job["job_uuid"], worker_id),
    )
    connection.execute(
        """
        UPDATE jobs
        SET status = 'succeeded', locked_by = NULL, locked_at = NULL,
            last_error = NULL, last_error_sanitized = NULL, run_after = NULL,
            updated_at = ?
        WHERE job_type = 'text_unitization'
          AND payload_jsonb->>'message_uuid' = ?
          AND status IN ('pending', 'running')
        """,
        (finished, message_uuid),
    )
    connection.execute(
        """
        UPDATE job_attempts
        SET status = 'succeeded', finished_at = ?, error_sanitized = NULL
        WHERE job_attempt_uuid = ?
        """,
        (finished, attempt_uuid),
    )


def _record_failure(
    connection: Any,
    job: dict[str, Any],
    attempt_uuid: str,
    worker_id: str,
    error: Exception,
) -> None:
    finished = utc_now()
    sanitized = sanitize_error_message(f"{type(error).__name__}: {error}") or type(error).__name__
    terminal = int(job.get("attempts") or 0) >= int(job.get("max_attempts") or 1)
    connection.execute(
        """
        UPDATE jobs
        SET status = ?, locked_by = NULL, locked_at = NULL,
            last_error = ?, last_error_sanitized = ?,
            run_after = CASE WHEN ? = 'pending' THEN now() + interval '1 second' ELSE NULL END,
            updated_at = ?
        WHERE job_uuid = ? AND locked_by = ?
        """,
        (
            "dead" if terminal else "pending",
            sanitized,
            sanitized,
            "dead" if terminal else "pending",
            finished,
            job["job_uuid"],
            worker_id,
        ),
    )
    connection.execute(
        """
        UPDATE job_attempts
        SET status = 'failed', finished_at = ?, error_sanitized = ?
        WHERE job_attempt_uuid = ?
        """,
        (finished, sanitized, attempt_uuid),
    )


def _job_payload(job: dict[str, Any]) -> dict[str, Any]:
    payload = job.get("payload_jsonb") or job.get("payload_ijson")
    if isinstance(payload, str):
        payload = json.loads(payload)
    if not isinstance(payload, dict) or not payload.get("message_uuid"):
        raise ValueError("memory extraction job is missing message_uuid")
    return payload


def _claim_next_extraction_job_sqlite(
    connection: Any,
    worker_id: str,
) -> dict[str, Any] | None:
    connection.execute("BEGIN IMMEDIATE")
    row = connection.execute(
        """
        SELECT * FROM jobs
        WHERE status = 'pending' AND job_type = 'memory_extraction'
          AND (run_after IS NULL OR run_after <= ?)
        ORDER BY priority DESC, COALESCE(run_after, created_at), created_at
        LIMIT 1
        """,
        (utc_now(),),
    ).fetchone()
    if row is None:
        connection.commit()
        return None
    cursor = connection.execute(
        """
        UPDATE jobs
        SET status = 'running', locked_by = ?, locked_at = ?, attempts = attempts + 1
        WHERE job_uuid = ? AND status = 'pending'
        """,
        (worker_id, utc_now(), row["job_uuid"]),
    )
    connection.commit()
    return dict(row) if cursor.rowcount == 1 else None


def _record_success_sqlite(
    connection: Any,
    job: dict[str, Any],
    message_uuid: str,
    worker_id: str,
) -> None:
    connection.execute(
        """
        UPDATE jobs
        SET status = 'succeeded', locked_by = NULL, locked_at = NULL,
            last_error = NULL, last_error_sanitized = NULL, run_after = NULL
        WHERE job_uuid = ? AND locked_by = ?
        """,
        (job["job_uuid"], worker_id),
    )
    connection.execute(
        """
        UPDATE jobs
        SET status = 'succeeded', locked_by = NULL, locked_at = NULL,
            last_error = NULL, last_error_sanitized = NULL, run_after = NULL
        WHERE job_type = 'text_unitization'
          AND payload_ijson LIKE ?
          AND status IN ('pending', 'running')
        """,
        (f'%"{message_uuid}"%',),
    )
    connection.commit()


def _record_failure_sqlite(
    connection: Any,
    job: dict[str, Any],
    worker_id: str,
    error: Exception,
) -> None:
    sanitized = sanitize_error_message(f"{type(error).__name__}: {error}") or type(error).__name__
    attempts = int(job.get("attempts") or 0) + 1
    terminal = attempts >= int(job.get("max_attempts") or 3)
    connection.execute(
        """
        UPDATE jobs
        SET status = ?, locked_by = NULL, locked_at = NULL,
            last_error = ?, last_error_sanitized = ?, run_after = ?
        WHERE job_uuid = ? AND locked_by = ?
        """,
        (
            "dead" if terminal else "pending",
            sanitized,
            sanitized,
            None if terminal else utc_now(),
            job["job_uuid"],
            worker_id,
        ),
    )
    connection.commit()
