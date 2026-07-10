from __future__ import annotations

import importlib
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, Literal

from memcore.model_control.security import sanitize_error_message
from memcore.storage.canonical.capabilities import StoreCapabilities
from memcore.storage.canonical.health import StoreHealth
from memcore.storage.postgres.migrations import apply_postgres_migrations, postgres_schema_version
from memcore.version import SCHEMA_VERSION


class PostgresCanonicalStore:
    store_kind: Literal["postgres"] = "postgres"

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    def health(self) -> StoreHealth:
        try:
            connection = self._connect()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT 1")
                    cursor.fetchone()
                schema_version = postgres_schema_version(connection)
            finally:
                connection.close()
            return StoreHealth(
                store_kind=self.store_kind,
                ok=True,
                status="ok",
                schema_version=schema_version or SCHEMA_VERSION,
            )
        except Exception as error:
            return StoreHealth(
                store_kind=self.store_kind,
                ok=False,
                status="error",
                error_sanitized=sanitize_error_message(str(error)),
            )

    def capabilities(self) -> StoreCapabilities:
        return StoreCapabilities(
            concurrent_writers=True,
            row_level_locking=True,
            skip_locked=True,
            json_native=True,
            full_text_search=True,
            advisory_locks=True,
            listen_notify=True,
            vector_extension=False,
        )

    @contextmanager
    def transaction(self) -> Iterator[Any]:
        connection = self._connect()
        try:
            with connection.transaction():
                yield connection
        finally:
            connection.close()

    def read_connection(self) -> Any:
        connection = self._connect()
        apply_postgres_migrations(connection)
        return connection

    def write_gateway(self) -> PostgresCanonicalStore:
        return self

    def migrate(self) -> None:
        connection = self._connect()
        try:
            apply_postgres_migrations(connection)
        finally:
            connection.close()

    def _connect(self) -> Any:
        psycopg = importlib.import_module("psycopg")
        return psycopg.connect(self.dsn)
