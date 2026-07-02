from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


def read_connection(db_path: str | Path, query_only: bool = True) -> sqlite3.Connection:
    connection = sqlite3.connect(Path(db_path), timeout=5.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    if query_only:
        connection.execute("PRAGMA query_only = ON")
    return connection


@contextmanager
def read_connection_context(
    db_path: str | Path,
    query_only: bool = True,
) -> Iterator[sqlite3.Connection]:
    connection = read_connection(db_path, query_only)
    try:
        yield connection
    finally:
        connection.close()
