from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Literal

from memcore.model_control.security import sanitize_error_message
from memcore.storage.canonical.capabilities import StoreCapabilities
from memcore.storage.canonical.health import StoreHealth
from memcore.storage.gateway import WriteGateway
from memcore.storage.migrations import apply_migrations
from memcore.storage.sqlite import connect
from memcore.version import SCHEMA_VERSION


class SQLiteCanonicalStore:
    store_kind: Literal["sqlite"] = "sqlite"

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    def health(self) -> StoreHealth:
        try:
            connection = connect(self.db_path)
            try:
                apply_migrations(connection)
                applied = connection.execute(
                    "SELECT COUNT(*) AS count FROM schema_migrations"
                ).fetchone()
                status = "ok" if applied is not None else "not_migrated"
            finally:
                connection.close()
            return StoreHealth(
                store_kind=self.store_kind,
                ok=True,
                status=status,
                schema_version=SCHEMA_VERSION,
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
            concurrent_writers=False,
            row_level_locking=False,
            skip_locked=False,
            json_native=False,
            full_text_search=True,
            advisory_locks=False,
            listen_notify=False,
            vector_extension=False,
        )

    @contextmanager
    def transaction(self) -> Iterator[Any]:
        connection = connect(self.db_path)
        try:
            apply_migrations(connection)
            with connection:
                yield connection
        finally:
            connection.close()

    def read_connection(self) -> Any:
        connection = connect(self.db_path)
        apply_migrations(connection)
        return connection

    def write_gateway(self) -> WriteGateway:
        return WriteGateway(str(Path(self.db_path)))
