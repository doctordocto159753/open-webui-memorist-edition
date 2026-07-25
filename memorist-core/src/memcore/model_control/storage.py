from __future__ import annotations

from typing import Any

from memcore.config import Settings, get_settings
from memcore.model_control.postgres_repository import PostgresModelControlRepository
from memcore.model_control.repository import ModelControlRepository

ModelControlStorage = ModelControlRepository | PostgresModelControlRepository


def uses_postgres_model_control(settings: Settings) -> bool:
    """Return the explicit canonical-store authority for model-control data."""

    return settings.runtime_profile == "full" and settings.canonical_store == "postgres"


def model_control_repository(
    connection: Any,
    settings: Settings | None = None,
) -> ModelControlStorage:
    """Construct the repository selected by the runtime storage authority.

    Consumers must not infer the database family from row values or from a
    compatibility adapter. Full mode owns JSONB decoding through the PostgreSQL
    repository. Keep ``PostgresCompatConnection`` when the caller supplied it:
    its cursor preserves column names for psycopg tuple rows while leaving
    PostgreSQL JSONB values native.
    """

    resolved = settings or get_settings()
    if uses_postgres_model_control(resolved):
        return PostgresModelControlRepository(connection)
    return ModelControlRepository(connection)
