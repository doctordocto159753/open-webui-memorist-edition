from __future__ import annotations

import json
import logging
import os
import socket
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from memcore.config import Settings
from memcore.imports.runtime import import_connection
from memcore.memory_worker.fencing import LeaseFenceRejected
from memcore.memory_worker.pipeline import MemoryWorkerPipeline
from memcore.memory_worker.postgres.pipeline import PostgresMemoryWorkerPipeline
from memcore.model_control.security import sanitize_error_message
from memcore.models import new_uuid, utc_now
from memcore.storage.sqlite import connect

LOGGER = logging.getLogger(__name__)
_POLL_SECONDS = 0.25


class JobLeaseFence:
    """Token/generation lease fence plus live source/profile identity checks."""

    def __init__(
        self,
        connection: Any,
        *,
        job: dict[str, Any],
        worker_id: str,
        message_uuid: str,
        lease_seconds: int,
        expected_snapshot: dict[str, str | None],
        current_snapshot: Callable[[], dict[str, str | None]],
        postgres: bool,
    ) -> None:
        self.connection = connection
        self.job_uuid = str(job["job_uuid"])
        self.worker_id = worker_id
        self.message_uuid = message_uuid
        self.lease_token = str(job["lease_token"])
        self.lease_generation = int(job["lease_generation"])
        self.lease_seconds = lease_seconds
        self.expected_snapshot = expected_snapshot
        self.current_snapshot = current_snapshot
        self.postgres = postgres

    def __call__(
        self,
        *,
        lock: bool = False,
        before_provider: bool = False,
    ) -> None:
        now = utc_now()
        lock_clause = " FOR UPDATE OF j" if lock and self.postgres else ""
        row = self.connection.execute(
            f"""
            SELECT j.job_uuid
            FROM jobs j
            WHERE j.job_uuid = ?
              AND j.job_type = 'memory_extraction'
              AND j.status = 'running'
              AND j.locked_by = ?
              AND j.lease_token = ?
              AND j.lease_generation = ?
              AND j.lease_expires_at IS NOT NULL
              AND j.lease_expires_at >= ?
            {lock_clause}
            """,
            (
                self.job_uuid,
                self.worker_id,
                self.lease_token,
                self.lease_generation,
                now,
            ),
        ).fetchone()
        if row is None:
            raise LeaseFenceRejected("memory processing lease is no longer owned")
        try:
            current = self.current_snapshot()
        except Exception as error:
            raise LeaseFenceRejected("memory processing source no longer exists") from error
        if current != self.expected_snapshot:
            raise LeaseFenceRejected(
                "memory processing input or effective profile changed during execution"
            )
        if before_provider:
            renewed = self.connection.execute(
                """
                UPDATE jobs
                SET lease_expires_at = ?, locked_at = ?, updated_at = ?
                WHERE job_uuid = ? AND status = 'running' AND locked_by = ?
                  AND lease_token = ? AND lease_generation = ?
                  AND lease_expires_at IS NOT NULL AND lease_expires_at >= ?
                """,
                (
                    _add_seconds(now, self.lease_seconds),
                    now,
                    now,
                    self.job_uuid,
                    self.worker_id,
                    self.lease_token,
                    self.lease_generation,
                    now,
                ),
            ).rowcount
            if renewed != 1:
                raise LeaseFenceRejected("memory processing lease renewal was rejected")


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
            job = _claim_next_extraction_job(
                connection,
                owner,
                self.settings.memory_worker_lease_seconds,
            )
            if job is None:
                return False
            attempt_uuid = _record_attempt(connection, job, owner)
            connection.commit()
            try:
                payload = _job_payload(job)
                message_uuid = str(payload["message_uuid"])
                pipeline = PostgresMemoryWorkerPipeline(connection, self.settings)
                expected_snapshot = pipeline.execution_snapshot(message_uuid)
                lease_fence = JobLeaseFence(
                    connection,
                    job=job,
                    worker_id=owner,
                    message_uuid=message_uuid,
                    lease_seconds=self.settings.memory_worker_lease_seconds,
                    expected_snapshot=expected_snapshot,
                    current_snapshot=lambda: pipeline.execution_snapshot(message_uuid),
                    postgres=True,
                )
                lease_fence(before_provider=True)
                connection.commit()
                prepared = pipeline.prepare_message(message_uuid)
                if (
                    prepared.input_content_hash != expected_snapshot["input_content_hash"]
                    or prepared.processing_identity_hash
                    != expected_snapshot["processing_identity_hash"]
                    or prepared.profile_fingerprint
                    != expected_snapshot["profile_fingerprint"]
                ):
                    raise LeaseFenceRejected(
                        "memory processing identity changed before provider completion"
                    )
                pipeline.process_message(
                    message_uuid,
                    job_uuid=str(job["job_uuid"]),
                    lease_fence=lease_fence,
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
            job = _claim_next_extraction_job_sqlite(
                connection,
                owner,
                self.settings.memory_worker_lease_seconds,
            )
            if job is None:
                return False
            try:
                payload = _job_payload(job)
                message_uuid = str(payload["message_uuid"])
                pipeline = MemoryWorkerPipeline(connection, self.settings)
                expected_snapshot = pipeline.execution_snapshot(message_uuid)
                lease_fence = JobLeaseFence(
                    connection,
                    job=job,
                    worker_id=owner,
                    message_uuid=message_uuid,
                    lease_seconds=self.settings.memory_worker_lease_seconds,
                    expected_snapshot=expected_snapshot,
                    current_snapshot=lambda: pipeline.execution_snapshot(message_uuid),
                    postgres=False,
                )
                lease_fence(before_provider=True)
                connection.commit()
                prepared = pipeline.prepare_message(message_uuid)
                if (
                    prepared.input_content_hash != expected_snapshot["input_content_hash"]
                    or prepared.processing_identity_hash
                    != expected_snapshot["processing_identity_hash"]
                    or prepared.profile_fingerprint
                    != expected_snapshot["profile_fingerprint"]
                ):
                    raise LeaseFenceRejected(
                        "memory processing identity changed before provider completion"
                    )
                pipeline.process_message(
                    message_uuid,
                    job_uuid=str(job["job_uuid"]),
                    lease_fence=lease_fence,
                    prepared_inference=prepared,
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
                        locked_by = NULL, locked_at = NULL, lease_token = NULL,
                        lease_expires_at = NULL,
                        last_error_sanitized = COALESCE(last_error_sanitized, 'lease expired')
                WHERE job_type = 'memory_extraction' AND status = 'running'
                      AND (lease_expires_at IS NULL OR lease_expires_at < ?)
                    """
                    ,
                    (utc_now(),),
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
                    locked_by = NULL, locked_at = NULL, lease_token = NULL,
                    lease_expires_at = NULL,
                    last_error_sanitized = COALESCE(last_error_sanitized, 'lease expired'),
                    updated_at = now()
                WHERE job_type = 'memory_extraction' AND status = 'running'
                  AND (lease_expires_at IS NULL OR lease_expires_at < now())
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


def _claim_next_extraction_job(
    connection: Any,
    worker_id: str,
    lease_seconds: int,
) -> dict[str, Any] | None:
    now = utc_now()
    connection.execute(
        """
        UPDATE jobs
        SET status = CASE WHEN attempts >= max_attempts THEN 'dead' ELSE 'pending' END,
            locked_by = NULL, locked_at = NULL, lease_token = NULL,
            lease_expires_at = NULL,
            last_error_sanitized = COALESCE(last_error_sanitized, 'lease expired'),
            updated_at = ?
        WHERE job_type = 'memory_extraction' AND status = 'running'
          AND (lease_expires_at IS NULL OR lease_expires_at < ?)
        """,
        (now, now),
    )
    lease_token = new_uuid()
    row = connection.execute(
        """
        UPDATE jobs
        SET status = 'running', locked_by = ?, locked_at = now(),
            lease_token = ?, lease_generation = lease_generation + 1,
            lease_expires_at = now() + (? * interval '1 second'),
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
        (worker_id, lease_token, lease_seconds),
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
    updated = connection.execute(
        """
        UPDATE jobs
        SET status = 'succeeded', locked_by = NULL, locked_at = NULL,
            lease_token = NULL, lease_expires_at = NULL,
            last_error = NULL, last_error_sanitized = NULL, run_after = NULL,
            updated_at = ?
        WHERE job_uuid = ? AND status = 'running' AND locked_by = ? AND lease_token = ?
          AND lease_generation = ? AND lease_expires_at >= ?
        """,
        (
            finished,
            job["job_uuid"],
            worker_id,
            job["lease_token"],
            job["lease_generation"],
            finished,
        ),
    ).rowcount
    if updated != 1:
        raise LeaseFenceRejected("memory job success write was fenced")
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
    updated = connection.execute(
        """
        UPDATE jobs
        SET status = ?, locked_by = NULL, locked_at = NULL,
            lease_token = NULL, lease_expires_at = NULL,
            last_error = ?, last_error_sanitized = ?,
            run_after = CASE WHEN ? = 'pending' THEN now() + interval '1 second' ELSE NULL END,
            updated_at = ?
        WHERE job_uuid = ? AND status = 'running' AND locked_by = ? AND lease_token = ?
          AND lease_generation = ? AND lease_expires_at >= ?
        """,
        (
            "dead" if terminal else "pending",
            sanitized,
            sanitized,
            "dead" if terminal else "pending",
            finished,
            job["job_uuid"],
            worker_id,
            job["lease_token"],
            job["lease_generation"],
            finished,
        ),
    ).rowcount
    if updated != 1:
        return
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
    lease_seconds: int,
) -> dict[str, Any] | None:
    connection.execute("BEGIN IMMEDIATE")
    now = utc_now()
    connection.execute(
        """
        UPDATE jobs
        SET status = CASE WHEN attempts >= max_attempts THEN 'dead' ELSE 'pending' END,
            locked_by = NULL, locked_at = NULL, lease_token = NULL,
            lease_expires_at = NULL,
            last_error_sanitized = COALESCE(last_error_sanitized, 'lease expired')
        WHERE job_type = 'memory_extraction' AND status = 'running'
          AND (lease_expires_at IS NULL OR lease_expires_at < ?)
        """,
        (now,),
    )
    row = connection.execute(
        """
        SELECT * FROM jobs
        WHERE status = 'pending' AND job_type = 'memory_extraction'
          AND (run_after IS NULL OR run_after <= ?)
        ORDER BY priority DESC, COALESCE(run_after, created_at), created_at
        LIMIT 1
        """,
        (now,),
    ).fetchone()
    if row is None:
        connection.commit()
        return None
    cursor = connection.execute(
        """
        UPDATE jobs
        SET status = 'running', locked_by = ?, locked_at = ?,
            lease_token = ?, lease_generation = lease_generation + 1,
            lease_expires_at = ?, attempts = attempts + 1
        WHERE job_uuid = ? AND status = 'pending'
        """,
        (
            worker_id,
            now,
            new_uuid(),
            _add_seconds(now, lease_seconds),
            row["job_uuid"],
        ),
    )
    claimed = connection.execute(
        "SELECT * FROM jobs WHERE job_uuid = ?",
        (row["job_uuid"],),
    ).fetchone()
    connection.commit()
    return dict(claimed) if cursor.rowcount == 1 and claimed is not None else None


def _record_success_sqlite(
    connection: Any,
    job: dict[str, Any],
    message_uuid: str,
    worker_id: str,
) -> None:
    updated = connection.execute(
        """
        UPDATE jobs
        SET status = 'succeeded', locked_by = NULL, locked_at = NULL,
            lease_token = NULL, lease_expires_at = NULL,
            last_error = NULL, last_error_sanitized = NULL, run_after = NULL
        WHERE job_uuid = ? AND status = 'running' AND locked_by = ? AND lease_token = ?
          AND lease_generation = ? AND lease_expires_at >= ?
        """,
        (
            job["job_uuid"],
            worker_id,
            job["lease_token"],
            job["lease_generation"],
            utc_now(),
        ),
    ).rowcount
    if updated != 1:
        raise LeaseFenceRejected("memory job success write was fenced")
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
    finished = utc_now()
    sanitized = sanitize_error_message(f"{type(error).__name__}: {error}") or type(error).__name__
    attempts = int(job.get("attempts") or 0)
    terminal = attempts >= int(job.get("max_attempts") or 3)
    updated = connection.execute(
        """
        UPDATE jobs
        SET status = ?, locked_by = NULL, locked_at = NULL,
            lease_token = NULL, lease_expires_at = NULL,
            last_error = ?, last_error_sanitized = ?, run_after = ?
        WHERE job_uuid = ? AND status = 'running' AND locked_by = ? AND lease_token = ?
          AND lease_generation = ? AND lease_expires_at >= ?
        """,
        (
            "dead" if terminal else "pending",
            sanitized,
            sanitized,
            None if terminal else finished,
            job["job_uuid"],
            worker_id,
            job["lease_token"],
            job["lease_generation"],
            finished,
        ),
    ).rowcount
    if updated != 1:
        connection.rollback()
        return
    connection.commit()


def _add_seconds(value: str, seconds: int) -> str:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return (
        (parsed + timedelta(seconds=seconds))
        .astimezone(UTC)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )
