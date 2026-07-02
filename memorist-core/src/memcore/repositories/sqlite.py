import re
import sqlite3
from collections.abc import Mapping, Sequence
from typing import Any

from memcore.storage.sqlite import with_busy_retry
from memcore.validators.payload_policy import prepare_repository_values

IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class SQLiteRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def insert(self, table_name: str, values: Mapping[str, Any]) -> sqlite3.Cursor:
        if not values:
            raise ValueError("insert requires at least one value")

        prepared_values = prepare_repository_values(values)
        columns = list(prepared_values)
        quoted_columns = ", ".join(_quote_identifier(column) for column in columns)
        placeholders = ", ".join("?" for _ in columns)
        quoted_table = _quote_identifier(table_name)
        sql = f"INSERT INTO {quoted_table} ({quoted_columns}) VALUES ({placeholders})"
        params = tuple(prepared_values[column] for column in columns)
        return with_busy_retry(lambda: self.connection.execute(sql, params))

    def update(
        self,
        table_name: str,
        values: Mapping[str, Any],
        where_clause: str,
        where_params: Sequence[Any] = (),
    ) -> sqlite3.Cursor:
        if not values:
            raise ValueError("update requires at least one value")
        if not where_clause.strip():
            raise ValueError("update requires a where clause")

        prepared_values = prepare_repository_values(values)
        columns = list(prepared_values)
        assignments = ", ".join(f"{_quote_identifier(column)} = ?" for column in columns)
        sql = f"UPDATE {_quote_identifier(table_name)} SET {assignments} WHERE {where_clause}"
        params = tuple(prepared_values[column] for column in columns) + tuple(where_params)
        return with_busy_retry(lambda: self.connection.execute(sql, params))


def _quote_identifier(identifier: str) -> str:
    if IDENTIFIER.fullmatch(identifier) is None:
        raise ValueError(f"Invalid SQLite identifier: {identifier}")
    return f'"{identifier}"'
