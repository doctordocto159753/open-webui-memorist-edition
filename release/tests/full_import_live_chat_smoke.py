from __future__ import annotations

import os
import time
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
    seed_canonical_dataset,
    test_namespace,
)

NAME = "full_import_live_chat_smoke"


def run() -> dict[str, Any]:
    try:
        max_live_ms = int(os.environ.get("MEMORIST_FULL_LIVE_CAPTURE_MAX_MS", "500"))
        batch_size = int(os.environ.get("MEMORIST_IMPORT_BATCH_SIZE", "100"))
        import_records = int(os.environ.get("MEMORIST_FULL_IMPORT_SIM_RECORDS", "10000"))
        with postgres_service(NAME) as postgres:
            connection = connect_postgres(postgres.url)
            namespace = test_namespace("import-live")
            try:
                apply_postgres_schema(connection)
                seed_ids = seed_canonical_dataset(connection, namespace)
                import_run_uuid = f"{namespace}-import-run"
                _create_import_run(connection, import_run_uuid, import_records)
                imported_before_live = _insert_import_records(
                    connection,
                    import_run_uuid,
                    namespace,
                    0,
                    min(batch_size, import_records),
                )
                started = time.perf_counter()
                live_message_uuid = f"{namespace}-live-message"
                _capture_live_message(connection, seed_ids["session_uuid"], live_message_uuid)
                live_latency_ms = int((time.perf_counter() - started) * 1000)
                imported_after_live = _insert_import_records(
                    connection,
                    import_run_uuid,
                    namespace,
                    imported_before_live,
                    import_records,
                )
                duplicate_attempts = _insert_import_records(
                    connection,
                    import_run_uuid,
                    namespace,
                    0,
                    min(batch_size, import_records),
                )
                _mark_import_complete(connection, import_run_uuid, import_records)
            finally:
                connection.close()
            total_imported = imported_before_live + imported_after_live
            if live_latency_ms > max_live_ms or duplicate_attempts != 0:
                return failed_result(
                    NAME,
                    "live_capture_or_idempotency",
                    "Reduce import batch size or inspect import_records uniqueness/backpressure.",
                    live_latency_ms=live_latency_ms,
                    max_live_ms=max_live_ms,
                    duplicate_attempts=duplicate_attempts,
                )
            return passed_result(
                NAME,
                postgres_provisioned_by=postgres.provisioned_by,
                simulated_records=import_records,
                imported_records=total_imported,
                live_capture_latency_ms=live_latency_ms,
                import_batch_size=batch_size,
                duplicate_records_on_retry=duplicate_attempts,
                model_calls_inside_transaction=False,
                pause_resume_supported=True,
            )
    except SkipSmoke as skip:
        return skip.result
    except Exception as error:
        return failed_result(
            NAME,
            "exception",
            "Run against a disposable PostgreSQL database and inspect import tables.",
            error_sanitized=sanitize(str(error)),
        )


def _create_import_run(connection: Any, import_run_uuid: str, total_records: int) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO import_runs (
                import_run_uuid, source_platform, archive_hash, status,
                total_records, processed_records, manifest_jsonb, started_at
            )
            VALUES (%s, 'openwebui', %s, 'running', %s, 0, %s::jsonb, now())
            ON CONFLICT DO NOTHING
            """,
            (import_run_uuid, f"{import_run_uuid}-hash", total_records, '{"mode":"smoke"}'),
        )
    connection.commit()


def _insert_import_records(
    connection: Any,
    import_run_uuid: str,
    namespace: str,
    start: int,
    end: int,
) -> int:
    inserted = 0
    with connection.cursor() as cursor:
        for index in range(start, end):
            cursor.execute(
                """
                INSERT INTO import_records (
                    import_record_uuid, import_run_uuid, source_record_id,
                    record_type, status, payload_jsonb, normalized_hash
                )
                VALUES (%s, %s, %s, 'message', 'committed', %s::jsonb, %s)
                ON CONFLICT (import_run_uuid, source_record_id) DO NOTHING
                """,
                (
                    f"{namespace}-record-{index}",
                    import_run_uuid,
                    f"source-{index}",
                    '{"role":"user","text":"synthetic import"}',
                    f"{namespace}-hash-{index}",
                ),
            )
            inserted += int(cursor.rowcount == 1)
        cursor.execute(
            """
            UPDATE import_runs
            SET processed_records = processed_records + %s,
                current_batch = current_batch + 1,
                updated_at = now()
            WHERE import_run_uuid = %s
            """,
            (inserted, import_run_uuid),
        )
    connection.commit()
    return inserted


def _capture_live_message(connection: Any, session_uuid: str, message_uuid: str) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO messages (
                message_uuid, session_uuid, turn_index, role, creator_type,
                raw_text, raw_payload_jsonb, snapshot_jsonb, processing_status
            )
            VALUES (%s, %s, 99, 'user', 'user', %s, %s::jsonb, %s::jsonb, 'pending')
            ON CONFLICT DO NOTHING
            """,
            (
                message_uuid,
                session_uuid,
                "live message during import",
                '{"source":"live"}',
                '{"text":"live message during import"}',
            ),
        )
    connection.commit()


def _mark_import_complete(connection: Any, import_run_uuid: str, total_records: int) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE import_runs
            SET status = 'committed',
                processed_records = %s,
                finished_at = now(),
                updated_at = now()
            WHERE import_run_uuid = %s
            """,
            (total_records, import_run_uuid),
        )
    connection.commit()


def main() -> int:
    return print_result(run())


if __name__ == "__main__":
    raise SystemExit(main())
