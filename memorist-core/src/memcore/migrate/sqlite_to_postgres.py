from __future__ import annotations

import hashlib
import importlib
import json
import sqlite3
from datetime import date, datetime
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
    "memory_gate_decisions",
    "jakobson_analysis_runs",
    "jakobson_sentence_annotations",
    "memory_signal_routes",
    "prompt_execution_runs",
    "semantic_coverage_runs",
    "memory_candidates",
    "candidate_evidence",
    "semantic_coverage_items",
    "semantic_candidate_links",
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
        content_mismatches: dict[str, dict[str, str]] = {}
        for table_name in MIGRATION_TABLES:
            if (
                table_name not in sqlite_counts
                or table_name not in postgres_counts
                or table_name in mismatches
            ):
                continue
            source_columns = [
                str(row["name"])
                for row in sqlite_connection.execute(f"PRAGMA table_info({table_name})")
            ]
            mapped_columns = _column_mapping(
                source_columns,
                _postgres_columns(postgres, table_name),
            )
            sqlite_digest, postgres_digest = _table_content_digests(
                sqlite_connection,
                postgres,
                table_name,
                mapped_columns,
            )
            if sqlite_digest != postgres_digest:
                content_mismatches[table_name] = {
                    "sqlite": sqlite_digest,
                    "postgres": postgres_digest,
                }
        return {
            "mode": "verify",
            "status": "pass" if not mismatches and not content_mismatches else "fail",
            "mismatches": mismatches,
            "content_mismatches": content_mismatches,
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
    primary_key_columns = _postgres_primary_key_columns(postgres, table_name)
    if not primary_key_columns:
        raise RuntimeError(f"target table {table_name} has no primary key for safe replay")
    inserted = 0
    with postgres.cursor() as cursor:
        for row in rows:
            values = [_map_value(row, source, target) for source, target in mapped_columns]
            insert_sql = (
                f"INSERT INTO {table_name} ({columns_sql}) VALUES ({placeholders}) "
                "ON CONFLICT DO NOTHING"
            )
            cursor.execute(insert_sql, values)
            if cursor.rowcount == 1:
                inserted += 1
                continue
            expected = dict(zip(target_names, values, strict=True))
            key_values = [expected.get(column) for column in primary_key_columns]
            if any(value is None for value in key_values):
                raise RuntimeError(
                    f"cannot validate conflicting {table_name} row without its primary key"
                )
            where = " AND ".join(f"{column} = %s" for column in primary_key_columns)
            cursor.execute(
                f"SELECT {columns_sql} FROM {table_name} WHERE {where}",
                key_values,
            )
            existing = cursor.fetchone()
            if existing is None or any(
                _canonical_compare_value(stored, source, target)
                != _canonical_compare_value(expected[target], source, target)
                for stored, (source, target) in zip(
                    existing,
                    mapped_columns,
                    strict=True,
                )
            ):
                raise RuntimeError(
                    f"target table {table_name} contains a conflicting canonical row"
                )
    return inserted


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


def _postgres_primary_key_columns(connection: Any, table_name: str) -> list[str]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT attribute.attname
            FROM pg_index idx
            JOIN pg_class relation ON relation.oid = idx.indrelid
            JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
            JOIN unnest(idx.indkey) WITH ORDINALITY AS key(attnum, position)
              ON true
            JOIN pg_attribute attribute
              ON attribute.attrelid = relation.oid
             AND attribute.attnum = key.attnum
            WHERE namespace.nspname = 'public'
              AND relation.relname = %s
              AND idx.indisprimary
            ORDER BY key.position
            """,
            (table_name,),
        )
        return [str(row[0]) for row in cursor.fetchall()]


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
    if target_column in {"is_deleted", "requires_high_confidence_pass"}:
        if isinstance(value, bool):
            return value
        if isinstance(value, int):
            return value != 0
        if isinstance(value, str):
            return value.lower() in {"1", "true", "t", "yes"}
    if source_column.endswith("_ijson") and target_column.endswith("_jsonb"):
        return json.dumps(load_ijson(str(value)), separators=(",", ":"))
    return value


def _table_content_digests(
    sqlite_connection: sqlite3.Connection,
    postgres: Any,
    table_name: str,
    mapped_columns: list[tuple[str, str]],
) -> tuple[str, str]:
    if not mapped_columns:
        return _rows_digest([]), _rows_digest([])
    source_names = ", ".join(source for source, _target in mapped_columns)
    target_names = ", ".join(target for _source, target in mapped_columns)
    sqlite_rows = sqlite_connection.execute(f"SELECT {source_names} FROM {table_name}").fetchall()
    with postgres.cursor() as cursor:
        cursor.execute(f"SELECT {target_names} FROM {table_name}")
        postgres_rows = cursor.fetchall()
    sqlite_values = [
        [_canonical_compare_value(row[source], source, target) for source, target in mapped_columns]
        for row in sqlite_rows
    ]
    postgres_values = [
        [
            _canonical_compare_value(value, source, target)
            for value, (source, target) in zip(row, mapped_columns, strict=True)
        ]
        for row in postgres_rows
    ]
    return _rows_digest(sqlite_values), _rows_digest(postgres_values)


def _rows_digest(rows: list[list[Any]]) -> str:
    encoded = [
        json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) for row in rows
    ]
    material = "\n".join(sorted(encoded)).encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def _canonical_compare_value(value: Any, source_column: str, target_column: str) -> Any:
    if value is None:
        return None
    if source_column.endswith("_ijson") and target_column.endswith("_jsonb"):
        if isinstance(value, str):
            return load_ijson(value)
        return value
    if isinstance(value, (datetime, date)):
        return _normalized_iso(value.isoformat())
    if source_column.endswith("_at") and isinstance(value, str):
        return _normalized_iso(value)
    if target_column in {"is_deleted", "requires_high_confidence_pass"}:
        if isinstance(value, str):
            return value.lower() in {"1", "true", "t", "yes"}
        return bool(value)
    if isinstance(value, bytes):
        return value.hex()
    return value


def _normalized_iso(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    return parsed.isoformat().replace("+00:00", "Z")


def _sqlite_table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
    return (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table_name,),
        ).fetchone()
        is not None
    )
