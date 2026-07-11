from __future__ import annotations

import importlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from memcore.storage.migrations import apply_migrations
from memcore.storage.postgres.migrations import apply_postgres_migrations
from memcore.storage.postgres.parity import DOCUMENTED_FULL_ONLY_TABLES, REQUIRED_CANONICAL_TABLES
from memcore.storage.sqlite import connect
from memcore.validators.ijson import load_ijson

MIGRATION_TABLES = [
    "workspaces",
    "projects",
    "sessions",
    "openwebui_session_aliases",
    "session_events",
    "model_profiles",
    "model_role_defaults",
    "model_usage_events",
    "jobs",
    "messages",
    "message_versions",
    "memory_processing_runs",
    "text_units",
    "jakobson_analysis_runs",
    "jakobson_sentence_annotations",
    "memory_signal_routes",
    "memory_candidates",
    "candidate_evidence",
    "memories",
    "memory_versions",
    "memory_evidence_links",
    "memory_blocks",
    "memory_block_versions",
    "memory_block_sources",
    "memory_context_attachments",
    "retrieval_runs",
    "retrieval_candidates",
    "import_runs",
    "import_records",
    "import_mappings",
    "privacy_requests",
    "privacy_request_items",
    "erasure_receipts",
    "graph_projection_outbox",
    "cost_events",
]


def dry_run(sqlite_path: str | Path) -> dict[str, Any]:
    connection = _sqlite_connection(sqlite_path)
    try:
        return {
            "mode": "dry-run",
            "sqlite": str(sqlite_path),
            "counts": _sqlite_counts(connection),
            "missing_required_tables": sorted(
                (REQUIRED_CANONICAL_TABLES - DOCUMENTED_FULL_ONLY_TABLES) - set(MIGRATION_TABLES)
            ),
            "backup_required": True,
        }
    finally:
        connection.close()


def commit(sqlite_path: str | Path, postgres_dsn: str) -> dict[str, Any]:
    sqlite_connection = _sqlite_connection(sqlite_path)
    postgres = _postgres_connection(postgres_dsn)
    try:
        apply_postgres_migrations(postgres)
        copied: dict[str, int] = {}
        for table_name in MIGRATION_TABLES:
            if not _sqlite_table_exists(sqlite_connection, table_name):
                continue
            copied[table_name] = _copy_table(sqlite_connection, postgres, table_name)
        postgres.commit()
        return {"mode": "commit", "copied": copied}
    finally:
        sqlite_connection.close()
        postgres.close()


def verify(sqlite_path: str | Path, postgres_dsn: str) -> dict[str, Any]:
    sqlite_connection = _sqlite_connection(sqlite_path)
    postgres = _postgres_connection(postgres_dsn)
    try:
        sqlite_counts = _sqlite_counts(sqlite_connection)
        postgres_counts = _postgres_counts(postgres)
        mismatches = {
            table_name: {
                "sqlite": sqlite_counts.get(table_name, 0),
                "postgres": postgres_counts.get(table_name, 0),
            }
            for table_name in sorted(set(sqlite_counts) | set(postgres_counts))
            if sqlite_counts.get(table_name, 0) != postgres_counts.get(table_name, 0)
        }
        return {
            "mode": "verify",
            "status": "pass" if not mismatches else "fail",
            "mismatches": mismatches,
        }
    finally:
        sqlite_connection.close()
        postgres.close()


def _sqlite_connection(sqlite_path: str | Path) -> sqlite3.Connection:
    connection = connect(sqlite_path)
    apply_migrations(connection)
    return connection


def _postgres_connection(postgres_dsn: str) -> Any:
    psycopg = importlib.import_module("psycopg")
    return psycopg.connect(postgres_dsn)


def _sqlite_counts(connection: sqlite3.Connection) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table_name in MIGRATION_TABLES:
        if _sqlite_table_exists(connection, table_name):
            counts[table_name] = int(
                connection.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
            )
    return counts


def _postgres_counts(connection: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    with connection.cursor() as cursor:
        for table_name in MIGRATION_TABLES:
            cursor.execute("SELECT to_regclass(%s)", (table_name,))
            if cursor.fetchone()[0] is None:
                continue
            cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
            counts[table_name] = int(cursor.fetchone()[0])
    return counts


def _copy_table(
    sqlite_connection: sqlite3.Connection,
    postgres: Any,
    table_name: str,
) -> int:
    source_columns = [
        str(row["name"]) for row in sqlite_connection.execute(f"PRAGMA table_info({table_name})")
    ]
    target_columns = _postgres_columns(postgres, table_name)
    mapped_columns = _column_mapping(source_columns, target_columns)
    if not mapped_columns:
        return 0
    select_columns = ", ".join(source for source, _target in mapped_columns)
    rows = sqlite_connection.execute(f"SELECT {select_columns} FROM {table_name}").fetchall()
    if not rows:
        return 0
    target_names = [target for _source, target in mapped_columns]
    placeholders = ", ".join(["%s"] * len(target_names))
    columns_sql = ", ".join(target_names)
    with postgres.cursor() as cursor:
        for row in rows:
            values = [_map_value(row, source, target) for source, target in mapped_columns]
            insert_sql = (
                f"INSERT INTO {table_name} ({columns_sql}) VALUES ({placeholders}) "
                "ON CONFLICT DO NOTHING"
            )
            cursor.execute(
                insert_sql,
                values,
            )
    return len(rows)


def _postgres_columns(connection: Any, table_name: str) -> set[str]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = %s
            """,
            (table_name,),
        )
        return {str(row[0]) for row in cursor.fetchall()}


def _column_mapping(source_columns: list[str], target_columns: set[str]) -> list[tuple[str, str]]:
    mapping: list[tuple[str, str]] = []
    for source in source_columns:
        if source in target_columns:
            mapping.append((source, source))
        elif source.endswith("_ijson"):
            jsonb_name = source.removesuffix("_ijson") + "_jsonb"
            if jsonb_name in target_columns:
                mapping.append((source, jsonb_name))
    return mapping


def _map_value(row: sqlite3.Row, source_column: str, target_column: str) -> Any:
    value = row[source_column]
    if target_column == "updated_at" and value is None:
        try:
            created_at = row["created_at"]
        except (IndexError, KeyError):
            created_at = None
        if created_at is not None:
            return created_at
    if value is None:
        return None
    if target_column in {"is_deleted"}:
        if isinstance(value, bool):
            return value
        if isinstance(value, int):
            return value != 0
        if isinstance(value, str):
            return value.lower() in {"1", "true", "t", "yes"}
    if source_column.endswith("_ijson") and target_column.endswith("_jsonb"):
        return json.dumps(load_ijson(str(value)), separators=(",", ":"))
    return value


def _sqlite_table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
    return (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table_name,),
        ).fetchone()
        is not None
    )
