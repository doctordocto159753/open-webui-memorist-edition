from memcore.config import Settings
from memcore.storage.canonical.base import CanonicalStore
from memcore.storage.canonical.postgres_store import PostgresCanonicalStore
from memcore.storage.canonical.sqlite_store import SQLiteCanonicalStore


def create_canonical_store(settings: Settings) -> CanonicalStore:
    if settings.canonical_store == "sqlite":
        return SQLiteCanonicalStore(settings.db_path)
    if settings.postgres_dsn is None:
        raise RuntimeError("postgres_dsn is required for PostgreSQL canonical store")
    return PostgresCanonicalStore(settings.postgres_dsn)


def assert_startup_ready(settings: Settings) -> None:
    store = create_canonical_store(settings)
    if settings.runtime_profile == "full" and store.store_kind != "postgres":
        raise RuntimeError("Full mode requires PostgreSQL canonical store")

    if store.store_kind == "postgres":
        migrate = getattr(store, "migrate", None)
        if callable(migrate):
            migrate()

    health = store.health()
    if not health.ok:
        raise RuntimeError(
            f"{store.store_kind} canonical store health check failed: "
            f"{health.error_sanitized or health.status}"
        )
