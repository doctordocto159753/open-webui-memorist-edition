from __future__ import annotations

import importlib
import re
from types import TracebackType
from typing import Any


class DualRow(dict[str, Any]):
    def __init__(self, data: dict[str, Any], order: list[str]) -> None:
        super().__init__(data)
        self._order = order

    def __getitem__(self, key: Any) -> Any:
        if isinstance(key, int):
            return super().__getitem__(self._order[key])
        return super().__getitem__(key)


def connect_compat(dsn: str) -> PostgresCompatConnection:
    psycopg = importlib.import_module("psycopg")
    return PostgresCompatConnection(psycopg.connect(dsn))


class CompatCursor:
    def __init__(self, cursor: Any) -> None:
        self.cursor = cursor

    def fetchone(self) -> Any:
        row = self.cursor.fetchone()
        return self._wrap(row) if row is not None else None

    @property
    def rowcount(self) -> int:
        return int(self.cursor.rowcount)

    def fetchall(self) -> list[Any]:
        return [self._wrap(row) for row in self.cursor.fetchall()]

    def __iter__(self) -> Any:
        for row in self.cursor:
            yield self._wrap(row)

    def _wrap(self, row: Any) -> Any:
        if self.cursor.description is None:
            return row
        order = [col.name for col in self.cursor.description]
        return DualRow(dict(zip(order, row, strict=False)), order)


class PostgresCompatConnection:
    """DB-API adapter that lets shared import repositories run on PostgreSQL."""

    def __init__(self, connection: Any) -> None:
        self.raw = connection

    def execute(self, sql: str, params: tuple[Any, ...] | list[Any] = ()) -> CompatCursor:
        translated = _translate_sql(sql)
        if not params:
            return CompatCursor(self.raw.execute(translated))
        return CompatCursor(
            self.raw.execute(translated, _normalize_insert_params(translated, params))
        )

    def close(self) -> None:
        self.raw.close()

    def commit(self) -> None:
        self.raw.commit()

    def rollback(self) -> None:
        self.raw.rollback()

    def __enter__(self) -> PostgresCompatConnection:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool | None:
        if exc_type is None:
            self.raw.commit()
        else:
            self.raw.rollback()
        return None


def _translate_sql(sql: str) -> str:
    translated = sql
    translated = re.sub(r"INSERT\s+OR\s+IGNORE", "INSERT", translated, flags=re.IGNORECASE)
    translated = re.sub(r"INSERT\s+OR\s+REPLACE", "INSERT", translated, flags=re.IGNORECASE)
    upper = translated.upper()
    if "INTO IMPORT_PROGRESS" in upper and "ON CONFLICT" not in upper:
        translated += " ON CONFLICT (import_run_uuid) DO NOTHING"
    if "INTO IMPORTED_CONVERSATIONS" in upper and "ON CONFLICT" not in upper:
        translated += " ON CONFLICT (import_run_uuid, conversation_fingerprint) DO NOTHING"
    if "INTO IMPORT_DRY_RUN_REPORTS" in upper and "ON CONFLICT" not in upper:
        translated += (
            " ON CONFLICT (import_run_uuid) DO UPDATE SET "
            "report_ijson = EXCLUDED.report_ijson, "
            "plan_fingerprint = EXCLUDED.plan_fingerprint, "
            "created_at = EXCLUDED.created_at, "
            "schema_version = EXCLUDED.schema_version"
        )
    return translated.replace("?", "%s")


def _normalize_insert_params(sql: str, params: tuple[Any, ...] | list[Any]) -> tuple[Any, ...]:
    """Preserve SQLite creation semantics on stricter PostgreSQL tables."""
    values = list(params)
    match = re.search(
        r"INSERT\s+INTO\s+[^\s(]+\s*\((?P<columns>[^)]+)\)\s*VALUES",
        sql,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if match is None:
        return tuple(values)
    columns = [item.strip().strip('"').lower() for item in match.group("columns").split(",")]
    if len(columns) != len(values):
        return tuple(values)
    if "updated_at" in columns and "created_at" in columns:
        updated_index = columns.index("updated_at")
        if values[updated_index] is None:
            values[updated_index] = values[columns.index("created_at")]
    return tuple(values)
