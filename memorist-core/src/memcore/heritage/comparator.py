from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from typing import Any

from memcore.heritage.package import DATA_TABLES
from memcore.imports.repositories import strip_none
from memcore.storage.migrations import apply_migrations
from memcore.storage.sqlite import connect
from memcore.validators.ijson import dump_ijson


def compare_databases(left_db_path: str | Path, right_db_path: str | Path) -> dict[str, Any]:
    left = connect(left_db_path)
    right = connect(right_db_path)
    try:
        apply_migrations(left)
        apply_migrations(right)
        tables: dict[str, Any] = {}
        issues: list[dict[str, Any]] = []
        for table in DATA_TABLES.values():
            left_hashes = _row_hashes(left, table)
            right_hashes = _row_hashes(right, table)
            table_result = {
                "left_count": len(left_hashes),
                "right_count": len(right_hashes),
                "left_hash": _collection_hash(left_hashes),
                "right_hash": _collection_hash(right_hashes),
            }
            table_result["equivalent"] = (
                table_result["left_count"] == table_result["right_count"]
                and table_result["left_hash"] == table_result["right_hash"]
            )
            tables[table] = table_result
            if not table_result["equivalent"]:
                issues.append(
                    {
                        "severity": "error",
                        "issue_code": "table_mismatch",
                        "table": table,
                        **table_result,
                    }
                )
        return {"equivalent": not issues, "tables": tables, "issues": issues}
    finally:
        left.close()
        right.close()


def _row_hashes(connection: sqlite3.Connection, table: str) -> list[str]:
    if not _table_exists(connection, table):
        return []
    rows = [dict(row) for row in connection.execute(f"SELECT * FROM {table}")]
    return sorted(
        hashlib.sha256(dump_ijson(strip_none(row)).encode("utf-8")).hexdigest() for row in rows
    )


def _collection_hash(hashes: list[str]) -> str:
    return hashlib.sha256("\n".join(hashes).encode("utf-8")).hexdigest()


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?",
        (table,),
    ).fetchone()
    return row is not None
