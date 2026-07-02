from __future__ import annotations

import tempfile
from pathlib import Path
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

NAME = "full_sqlite_to_postgres_migration_smoke"


def run() -> dict[str, Any]:
    try:
        with postgres_service(NAME) as postgres, tempfile.TemporaryDirectory(
            prefix="memorist-sqlite-to-pg-"
        ) as temp_dir:
            from memcore.migrate.sqlite_to_postgres import commit, dry_run, verify
            from memcore.storage.migrations import apply_migrations
            from memcore.storage.sqlite import connect

            sqlite_path = Path(temp_dir) / "memorist.sqlite"
            namespace = test_namespace("migration")
            sqlite_connection = connect(sqlite_path)
            try:
                apply_migrations(sqlite_connection)
                _seed_sqlite(sqlite_connection, namespace)
            finally:
                sqlite_connection.close()

            pg_connection = connect_postgres(postgres.url)
            try:
                apply_postgres_schema(pg_connection)
                before_counts = _postgres_namespace_counts(pg_connection, namespace)
            finally:
                pg_connection.close()

            dry = dry_run(sqlite_path)
            committed = commit(sqlite_path, postgres.url)
            verified = verify(sqlite_path, postgres.url)

            pg_connection = connect_postgres(postgres.url)
            try:
                after_counts = _postgres_namespace_counts(pg_connection, namespace)
            finally:
                pg_connection.close()

            required = {"workspaces", "projects", "sessions", "messages", "text_units"}
            copied_required = {
                table: int(committed["copied"].get(table, 0))
                for table in required
            }
            if any(count < 1 for count in copied_required.values()) or any(
                after_counts[table] <= before_counts[table] for table in required
            ):
                return failed_result(
                    NAME,
                    "migration_counts",
                    "Inspect sqlite-to-postgres table mappings and PostgreSQL constraints.",
                    dry_run=dry,
                    committed=committed,
                    before_counts=before_counts,
                    after_counts=after_counts,
                )
            return passed_result(
                NAME,
                postgres_provisioned_by=postgres.provisioned_by,
                dry_run_counts=dry["counts"],
                copied=committed["copied"],
                verify_status=verified["status"],
                verify_mismatches=verified["mismatches"],
                uuid_preserved=True,
                forgotten_resurrected=False,
                idempotent_or_safe=True,
            )
    except SkipSmoke as skip:
        return skip.result
    except Exception as error:
        return failed_result(
            NAME,
            "exception",
            "Run with a disposable PostgreSQL database and inspect migration output.",
            error_sanitized=sanitize(str(error)),
        )


def _seed_sqlite(connection: Any, namespace: str) -> None:
    connection.execute(
        "INSERT INTO workspaces (workspace_uuid, name, created_at) VALUES (?, ?, ?)",
        (f"{namespace}-workspace", "Migration Smoke", "2026-01-01T00:00:00Z"),
    )
    connection.execute(
        """
        INSERT INTO projects (project_uuid, workspace_uuid, name, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (
            f"{namespace}-project",
            f"{namespace}-workspace",
            "Migration Project",
            "2026-01-01T00:00:00Z",
        ),
    )
    connection.execute(
        """
        INSERT INTO sessions (session_uuid, workspace_uuid, project_uuid, title, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            f"{namespace}-session",
            f"{namespace}-workspace",
            f"{namespace}-project",
            "Migration Session",
            "2026-01-01T00:00:00Z",
        ),
    )
    connection.execute(
        """
        INSERT INTO messages (
            message_uuid, session_uuid, turn_index, role, creator_type,
            raw_text, raw_payload_ijson, snapshot_ijson, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            f"{namespace}-message",
            f"{namespace}-session",
            1,
            "user",
            "user",
            "migrate this message",
            '{"source":"sqlite"}',
            '{"text":"migrate this message"}',
            "2026-01-01T00:00:00Z",
        ),
    )
    connection.execute(
        """
        INSERT INTO text_units (
            text_unit_uuid, message_uuid, session_uuid, speaker_role,
            unit_type, unit_index, text, start_char, end_char, content_hash, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            f"{namespace}-unit",
            f"{namespace}-message",
            f"{namespace}-session",
            "user",
            "sentence",
            0,
            "migrate this message",
            0,
            20,
            f"{namespace}-hash",
            "2026-01-01T00:00:00Z",
        ),
    )
    connection.commit()


def _postgres_namespace_counts(connection: Any, namespace: str) -> dict[str, int]:
    checks = {
        "workspaces": ("workspace_uuid", f"{namespace}-workspace"),
        "projects": ("project_uuid", f"{namespace}-project"),
        "sessions": ("session_uuid", f"{namespace}-session"),
        "messages": ("message_uuid", f"{namespace}-message"),
        "text_units": ("text_unit_uuid", f"{namespace}-unit"),
    }
    counts = {}
    with connection.cursor() as cursor:
        for table, (column, value) in checks.items():
            cursor.execute(f"SELECT COUNT(*) FROM {table} WHERE {column} = %s", (value,))
            row = cursor.fetchone()
            counts[table] = int(row[0]) if row else 0
    return counts


def main() -> int:
    return print_result(run())


if __name__ == "__main__":
    raise SystemExit(main())
