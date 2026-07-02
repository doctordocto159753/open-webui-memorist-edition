from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from typing import Any

from release.tests.full_mode_common import (
    SkipSmoke,
    apply_postgres_schema,
    connect_postgres,
    failed_result,
    passed_result,
    postgres_service,
    print_result,
    sanitize,
    test_namespace,
)

NAME = "full_postgres_job_concurrency"
OUTBOX_TABLES = ("graph_projection_outbox", "embedding_outbox", "privacy_erasure_outbox")


def run() -> dict[str, Any]:
    try:
        with postgres_service(NAME) as postgres:
            from memcore.storage.postgres.jobs import PostgresJobRepository
            from memcore.storage.postgres.outbox import PostgresOutboxRepository

            seed_connection = connect_postgres(postgres.url)
            namespace = test_namespace("jobs")
            try:
                apply_postgres_schema(seed_connection)
                jobs = PostgresJobRepository(seed_connection)
                for index in range(100):
                    jobs.enqueue_job(
                        "full_smoke_job",
                        {"index": index, "namespace": namespace},
                        priority=90 if index < 20 else 20,
                        idempotency_key=f"{namespace}-job-{index}",
                    )
                _insert_stale_job(seed_connection, namespace)
                outbox_inserted = {}
                for table_name in OUTBOX_TABLES:
                    outbox = PostgresOutboxRepository(seed_connection, table_name)  # type: ignore[arg-type]
                    outbox_inserted[table_name] = []
                    for index in range(24):
                        row = outbox.enqueue(
                            "full_smoke_outbox",
                            "memory_version",
                            f"{namespace}-{table_name}-{index}",
                            {"index": index, "namespace": namespace},
                            priority=80 if index < 8 else 20,
                        )
                        outbox_inserted[table_name].append(row["outbox_uuid"])
            finally:
                seed_connection.close()

            claimed_jobs = _claim_jobs_concurrently(postgres.url, worker_count=8)
            duplicate_jobs = _duplicates([str(item["job_uuid"]) for item in claimed_jobs])
            recovered = _recover_stale(postgres.url)
            outbox_claims = {
                table_name: _claim_outbox_concurrently(postgres.url, table_name, worker_count=4)
                for table_name in OUTBOX_TABLES
            }
            duplicate_outbox = {
                table_name: _duplicates([str(item["outbox_uuid"]) for item in rows])
                for table_name, rows in outbox_claims.items()
            }
            high_priority_claims = [
                int(item["priority"]) for item in claimed_jobs if int(item["priority"]) >= 90
            ]
            low_priority_before_high = _low_priority_before_high(claimed_jobs)
            failures = {
                "duplicate_jobs": duplicate_jobs,
                "duplicate_outbox": duplicate_outbox,
                "low_priority_before_high": low_priority_before_high,
                "recovered_count": len(recovered),
            }
            if duplicate_jobs or any(duplicate_outbox.values()) or len(recovered) < 1:
                return failed_result(
                    NAME,
                    "concurrency_assertions",
                    "Inspect PostgreSQL SKIP LOCKED job/outbox SQL and stale recovery.",
                    **failures,
                )
            return passed_result(
                NAME,
                postgres_provisioned_by=postgres.provisioned_by,
                claimed_job_count=len(claimed_jobs),
                high_priority_claim_count=len(high_priority_claims),
                low_priority_before_high=low_priority_before_high,
                recovered_stale_jobs=len(recovered),
                outbox_claim_counts={table: len(rows) for table, rows in outbox_claims.items()},
            )
    except SkipSmoke as skip:
        return skip.result
    except Exception as error:
        return failed_result(
            NAME,
            "exception",
            "Run against a disposable PostgreSQL database with `MEMORIST_TEST_POSTGRES_DSN`.",
            error_sanitized=sanitize(str(error)),
        )


def _claim_jobs_concurrently(dsn: str, worker_count: int) -> list[dict[str, Any]]:
    from memcore.storage.postgres.jobs import PostgresJobRepository

    claimed: list[dict[str, Any]] = []
    lock = threading.Lock()

    def worker(worker_index: int) -> None:
        connection = connect_postgres(dsn)
        try:
            repository = PostgresJobRepository(connection)
            while True:
                row = repository.claim_next_job(f"worker-{worker_index}")
                if row is None:
                    return
                if str(row["job_type"]) == "full_smoke_job":
                    with lock:
                        claimed.append(row)
        finally:
            connection.close()

    threads = [threading.Thread(target=worker, args=(index,), daemon=True) for index in range(worker_count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    return claimed


def _claim_outbox_concurrently(dsn: str, table_name: str, worker_count: int) -> list[dict[str, Any]]:
    from memcore.storage.postgres.outbox import PostgresOutboxRepository

    claimed: list[dict[str, Any]] = []
    lock = threading.Lock()

    def worker(worker_index: int) -> None:
        connection = connect_postgres(dsn)
        try:
            repository = PostgresOutboxRepository(connection, table_name)  # type: ignore[arg-type]
            while True:
                row = repository.claim_next(f"outbox-worker-{worker_index}")
                if row is None:
                    return
                if str(row["event_type"]) == "full_smoke_outbox":
                    with lock:
                        claimed.append(row)
        finally:
            connection.close()

    threads = [threading.Thread(target=worker, args=(index,), daemon=True) for index in range(worker_count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    return claimed


def _insert_stale_job(connection: Any, namespace: str) -> None:
    locked_at = datetime.now(UTC) - timedelta(hours=1)
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO jobs (
                job_uuid, job_type, priority, status, payload_jsonb,
                idempotency_key, run_after, attempts, max_attempts, locked_by, locked_at
            )
            VALUES (%s, 'full_smoke_stale_job', 95, 'running', %s::jsonb, %s, now(), 1, 3, 'stale', %s)
            ON CONFLICT DO NOTHING
            """,
            (
                f"{namespace}-stale-job",
                '{"namespace":"stale"}',
                f"{namespace}-stale-job",
                locked_at,
            ),
        )
    connection.commit()


def _recover_stale(dsn: str) -> list[dict[str, Any]]:
    from memcore.storage.postgres.jobs import PostgresJobRepository

    connection = connect_postgres(dsn)
    try:
        return [
            row
            for row in PostgresJobRepository(connection).recover_stale_jobs(
                lease_timeout_seconds=60
            )
            if str(row["job_type"]) == "full_smoke_stale_job"
        ]
    finally:
        connection.close()


def _duplicates(values: list[str]) -> list[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return sorted(duplicates)


def _low_priority_before_high(claimed_jobs: list[dict[str, Any]]) -> bool:
    seen_low = False
    for row in claimed_jobs:
        priority = int(row["priority"])
        if priority < 90:
            seen_low = True
        if seen_low and priority >= 90:
            return True
    return False


def main() -> int:
    return print_result(run())


if __name__ == "__main__":
    raise SystemExit(main())
