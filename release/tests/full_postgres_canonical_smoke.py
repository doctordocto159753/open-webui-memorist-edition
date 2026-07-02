from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from release.tests.full_mode_common import (
    SkipSmoke,
    apply_postgres_schema,
    connect_postgres,
    failed_result,
    fetch_count,
    passed_result,
    postgres_service,
    print_result,
    sanitize,
    seed_canonical_dataset,
    test_namespace,
)

NAME = "full_postgres_canonical_smoke"


def run() -> dict[str, Any]:
    try:
        with postgres_service(NAME) as postgres:
            with tempfile.TemporaryDirectory(prefix="memorist-full-pg-smoke-") as temp_dir:
                from memcore.config import Settings
                from memcore.storage.canonical.factory import create_canonical_store

                sqlite_marker = Path(temp_dir) / "must-not-be-created.sqlite"
                settings = Settings(
                    runtime_profile="full",
                    canonical_store="postgres",
                    postgres_dsn=postgres.url,
                    db_path=str(sqlite_marker),
                    graph_backend="disabled",
                    allow_full_graph_degraded=True,
                    hot_scheduler="in_memory",
                    object_store_path=str(Path(temp_dir) / "objects"),
                )
                store = create_canonical_store(settings)
                health = store.health()
                if store.store_kind != "postgres" or not health.ok:
                    return failed_result(
                        NAME,
                        "postgres_health",
                        "Check MEMORIST_TEST_POSTGRES_DSN and PostgreSQL connectivity.",
                        health=health.model_dump(mode="json"),
                    )
                connection = connect_postgres(postgres.url)
                try:
                    schema_version = apply_postgres_schema(connection)
                    namespace = test_namespace("canonical")
                    ids = seed_canonical_dataset(connection, namespace)
                    required_counts = {
                        "workspaces": fetch_count(
                            connection, "workspaces", "workspace_uuid", ids["workspace_uuid"]
                        ),
                        "projects": fetch_count(
                            connection, "projects", "project_uuid", ids["project_uuid"]
                        ),
                        "sessions": fetch_count(
                            connection, "sessions", "session_uuid", ids["session_uuid"]
                        ),
                        "messages": fetch_count(
                            connection, "messages", "message_uuid", ids["message_uuid"]
                        ),
                        "text_units": fetch_count(
                            connection, "text_units", "text_unit_uuid", ids["text_unit_uuid"]
                        ),
                        "jakobson_analysis_runs": fetch_count(
                            connection,
                            "jakobson_analysis_runs",
                            "analysis_run_uuid",
                            ids["analysis_run_uuid"],
                        ),
                        "jakobson_sentence_annotations": fetch_count(
                            connection,
                            "jakobson_sentence_annotations",
                            "annotation_uuid",
                            ids["annotation_uuid"],
                        ),
                        "memory_signal_routes": fetch_count(
                            connection,
                            "memory_signal_routes",
                            "route_uuid",
                            ids["route_uuid"],
                        ),
                        "memory_candidates": fetch_count(
                            connection,
                            "memory_candidates",
                            "candidate_uuid",
                            ids["candidate_uuid"],
                        ),
                        "memory_versions": fetch_count(
                            connection,
                            "memory_versions",
                            "memory_version_uuid",
                            ids["memory_version_uuid"],
                        ),
                        "model_usage_events": fetch_count(
                            connection,
                            "model_usage_events",
                            "usage_event_uuid",
                            ids["usage_event_uuid"],
                        ),
                    }
                finally:
                    connection.close()
                missing = [table for table, count in required_counts.items() if count != 1]
                if missing:
                    return failed_result(
                        NAME,
                        "canonical_row_verification",
                        "Run PostgreSQL migrations and inspect failed table counts.",
                        required_counts=required_counts,
                        missing=missing,
                    )
                if sqlite_marker.exists():
                    return failed_result(
                        NAME,
                        "sqlite_fallback_detected",
                        "Full Mode must not create or write a SQLite canonical store.",
                        sqlite_path=str(sqlite_marker),
                    )
                return passed_result(
                    NAME,
                    postgres_provisioned_by=postgres.provisioned_by,
                    schema_version=schema_version,
                    canonical_store="postgres",
                    sqlite_canonical_used=False,
                    required_counts=required_counts,
                )
    except SkipSmoke as skip:
        return skip.result
    except Exception as error:
        return failed_result(
            NAME,
            "exception",
            "Run with `cd memorist-core && uv run python ../release/tests/full_postgres_canonical_smoke.py`.",
            error_sanitized=sanitize(str(error)),
        )


def main() -> int:
    return print_result(run())


if __name__ == "__main__":
    raise SystemExit(main())
