"""PRD-02 WP01: the polarity column exists and behaves the same in Full mode.

Gated on a real PostgreSQL. Proves the Full migration lands the same additive,
default-``unknown`` column as the SQLite one, so Lite and Full store the same
semantic decision in the same shape.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from memcore.config import Settings
from memcore.imports.runtime import import_connection, initialize_runtime_storage
from memcore.textsemantics import Polarity

pytestmark = pytest.mark.skipif(
    not os.getenv("MEMORIST_POSTGRES_DSN"),
    reason="requires real PostgreSQL",
)


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        env="test",
        runtime_profile="full",
        canonical_store="postgres",
        postgres_dsn=os.environ["MEMORIST_POSTGRES_DSN"],
        object_store_path=str(tmp_path / "objects"),
        db_path=str(tmp_path / "unused.sqlite3"),
        graph_backend="disabled",
        allow_full_graph_degraded=True,
        hot_scheduler="in_memory",
    )


def _column(connection: Any, table: str) -> dict[str, Any] | None:
    row = connection.execute(
        """
        SELECT data_type, is_nullable, column_default
        FROM information_schema.columns
        WHERE table_name = %s AND column_name = 'polarity'
        """,
        (table,),
    ).fetchone()
    return dict(row) if row is not None else None


@pytest.mark.parametrize("table", ["memory_candidates", "memory_versions"])
def test_full_mode_migration_adds_the_polarity_column(tmp_path: Path, table: str) -> None:
    settings = _settings(tmp_path)
    initialize_runtime_storage(settings)

    with import_connection(settings) as connection:
        column = _column(connection, table)

    assert column is not None, f"{table}.polarity must exist in Full mode"
    assert column["data_type"] == "text"
    assert column["is_nullable"] == "NO"
    assert "unknown" in str(column["column_default"])


def test_full_mode_migration_is_idempotent(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    for _ in range(3):
        initialize_runtime_storage(settings)

    with import_connection(settings) as connection:
        row = connection.execute(
            """
            SELECT count(*) AS n
            FROM information_schema.columns
            WHERE table_name = 'memory_candidates' AND column_name = 'polarity'
            """,
            (),
        ).fetchone()

    assert int(dict(row)["n"]) == 1


def test_full_mode_rows_default_to_unknown_polarity(tmp_path: Path) -> None:
    """A row inserted without polarity is unknown, never assumed affirmed."""

    settings = _settings(tmp_path)
    initialize_runtime_storage(settings)

    with import_connection(settings) as connection:
        row = connection.execute(
            """
            SELECT column_default
            FROM information_schema.columns
            WHERE table_name = 'memory_candidates' AND column_name = 'polarity'
            """,
            (),
        ).fetchone()

    assert Polarity.UNKNOWN.value in str(dict(row)["column_default"])
