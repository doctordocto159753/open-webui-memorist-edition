import sqlite3
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path


def connect(db_path: Path | str) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(path, timeout=5.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA busy_timeout = 5000")
    connection.execute("PRAGMA synchronous = NORMAL")
    return connection


def is_sqlite_busy(error: sqlite3.OperationalError) -> bool:
    message = str(error).lower()
    return "database is locked" in message or "database table is locked" in message


def with_busy_retry[ResultT](
    operation: Callable[[], ResultT],
    max_attempts: int = 4,
    base_delay_seconds: float = 0.05,
) -> ResultT:
    for attempt in range(max_attempts):
        try:
            return operation()
        except sqlite3.OperationalError as error:
            if not is_sqlite_busy(error) or attempt + 1 >= max_attempts:
                raise
            time.sleep(base_delay_seconds * (2**attempt))
    raise RuntimeError("unreachable SQLite busy retry state")


@contextmanager
def sqlite_connection(db_path: Path | str) -> Iterator[sqlite3.Connection]:
    connection = connect(db_path)
    try:
        yield connection
    finally:
        connection.close()
