from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from memcore.config import Settings
from memcore.storage.migrations import apply_migrations
from memcore.storage.postgres.compat import connect_compat
from memcore.storage.postgres.migrations import apply_postgres_migrations
from memcore.storage.sqlite import connect


def initialize_import_storage(settings: Settings) -> None:
    """Apply import/runtime schema migrations during explicit startup only."""
    if settings.runtime_profile == "lite" and settings.canonical_store == "sqlite":
        sqlite_connection = connect(settings.db_path)
        try:
            apply_migrations(sqlite_connection)
        finally:
            sqlite_connection.close()
        return
    if settings.runtime_profile == "full" and settings.canonical_store == "postgres":
        if not settings.postgres_dsn:
            raise RuntimeError("postgres_dsn is required for Full/PostgreSQL import runtime")
        pg_connection = connect_compat(settings.postgres_dsn)
        try:
            apply_postgres_migrations(pg_connection.raw)
        finally:
            pg_connection.close()
        return
    raise RuntimeError(
        "unsupported import runtime: expected lite/sqlite or full/postgres; "
        f"got {settings.runtime_profile}/{settings.canonical_store}"
    )


@contextmanager
def import_connection(settings: Settings) -> Iterator[Any]:
    if settings.runtime_profile == "lite" and settings.canonical_store == "sqlite":
        sqlite_connection = connect(settings.db_path)
        try:
            yield sqlite_connection
        finally:
            sqlite_connection.close()
        return
    if settings.runtime_profile == "full" and settings.canonical_store == "postgres":
        if not settings.postgres_dsn:
            raise RuntimeError("postgres_dsn is required for Full/PostgreSQL import runtime")
        pg_connection = connect_compat(settings.postgres_dsn)
        try:
            yield pg_connection
        finally:
            pg_connection.close()
        return
    raise RuntimeError(
        "unsupported import runtime: expected lite/sqlite or full/postgres; "
        f"got {settings.runtime_profile}/{settings.canonical_store}"
    )
