from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any


@contextmanager
def null_transaction(connection: Any) -> Iterator[Any]:
    try:
        yield connection
        commit = getattr(connection, "commit", None)
        if callable(commit):
            commit()
    except Exception:
        rollback = getattr(connection, "rollback", None)
        if callable(rollback):
            rollback()
        raise
