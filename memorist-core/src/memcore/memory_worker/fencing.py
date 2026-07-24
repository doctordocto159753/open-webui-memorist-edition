from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any, Protocol

from memcore.storage.sqlite import NestedTransactionConnection


class LeaseFenceRejected(RuntimeError):
    """Raised when a worker no longer owns the canonical-write lease."""


class LeaseFence(Protocol):
    def __call__(
        self,
        *,
        lock: bool = False,
        before_provider: bool = False,
    ) -> None: ...


def invoke_lease_fence(
    lease_fence: Callable[..., None] | None,
    *,
    lock: bool = False,
    before_provider: bool = False,
) -> None:
    if lease_fence is None:
        return
    try:
        lease_fence(lock=lock, before_provider=before_provider)
    except TypeError as error:
        # Backward-compatible support for the pre-fencing no-argument callback
        # used by older callers. Do not mask TypeError raised by the callback.
        try:
            lease_fence()
        except TypeError:
            raise error from None


def close_transaction_before_provider(
    connection: Any,
    lease_fence: Callable[..., None] | None = None,
) -> None:
    """End any read/write transaction before a potentially slow provider call."""

    connection.commit()
    invoke_lease_fence(lease_fence, before_provider=True)
    connection.commit()


@contextmanager
def fenced_write(
    connection: Any,
    lease_fence: Callable[..., None] | None = None,
    *,
    postgres: bool = False,
) -> Iterator[None]:
    """Open one short write transaction and lock/revalidate its lease fence."""

    if postgres:
        raw = getattr(connection, "raw", None)
        if raw is None:
            raise RuntimeError("PostgreSQL fenced write requires a raw connection")
        connection.commit()
        with raw.transaction():
            invoke_lease_fence(lease_fence, lock=True)
            yield
        return

    if isinstance(connection, NestedTransactionConnection):
        if not connection.composed_write_active:
            connection.commit()
        with connection.atomic(immediate=True):
            invoke_lease_fence(lease_fence, lock=True)
            yield
        return

    if not isinstance(connection, sqlite3.Connection):
        raise RuntimeError("unsupported fenced-write connection")
    with connection:
        invoke_lease_fence(lease_fence, lock=True)
        yield
