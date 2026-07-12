from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from types import TracebackType
from typing import Literal


class NestedTransactionConnection(sqlite3.Connection):
    """SQLite connection with an opt-in outer atomic transaction.

    Repository methods use ``with connection`` and would normally commit at each
    method boundary. During ``atomic()`` those nested contexts are suppressed so
    one short fenced import-result transaction owns the final commit. Outside
    ``atomic()`` the standard sqlite3 context-manager behavior is unchanged.
    """

    _atomic_depth = 0
    _rollback_only = False

    def __enter__(self) -> NestedTransactionConnection:
        if self._atomic_depth > 0:
            return self
        return super().__enter__()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> Literal[False]:
        if self._atomic_depth > 0:
            if exc_type is not None:
                self._rollback_only = True
            return False
        return super().__exit__(exc_type, exc, tb)

    @contextmanager
    def atomic(self, *, immediate: bool = False) -> Iterator[None]:
        if self._atomic_depth > 0:
            self._atomic_depth += 1
            try:
                yield
            except BaseException:
                self._rollback_only = True
                raise
            finally:
                self._atomic_depth -= 1
            return

        self.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
        self._atomic_depth = 1
        self._rollback_only = False
        try:
            yield
            if self._rollback_only:
                self.rollback()
                raise RuntimeError("SQLite atomic transaction was marked rollback-only")
            self.commit()
        except BaseException:
            self.rollback()
            raise
        finally:
            self._atomic_depth = 0
            self._rollback_only = False


def connect(db_path: Path | str) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(path, timeout=5.0, factory=NestedTransactionConnection)
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
