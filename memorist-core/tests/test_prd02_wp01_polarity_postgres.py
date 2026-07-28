"""PRD-02 WP01: polarity storage and authority behavior in Full mode."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from memcore.config import Settings
from memcore.imports.runtime import import_connection, initialize_runtime_storage
from memcore.memory_worker.analysis.modality import NON_AUTHORITATIVE_LEXICAL_HINT
from memcore.memory_worker.postgres.pipeline import PostgresMemoryWorkerPipeline
from memcore.models import ModelRole, utc_now
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


def test_deterministic_full_pipeline_does_not_persist_lexical_polarity_hint(
    tmp_path: Path,
) -> None:
    """No model analysis means canonical polarity remains ``unknown``.

    The deterministic analyzer may record a lexical hint for diagnostics, but
    the hint is stamped non-authoritative. Candidates and durable memory
    versions must therefore fail closed to ``unknown`` until WP02 supplies a
    validated model semantic unit.
    """

    text = "Do not disable PostgreSQL backups."
    settings = _settings(tmp_path)
    initialize_runtime_storage(settings)

    with import_connection(settings) as connection:
        message_uuid = _seed_message(connection, text)
        pipeline = PostgresMemoryWorkerPipeline(connection, settings)
        pipeline.process_message(
            message_uuid,
            model_target={
                "model_role": ModelRole.MEMORY_EXTRACTION.value,
                "provider_type": "deterministic",
                "model_name": "deterministic_extraction",
            },
        )
        connection.commit()

        modality = connection.execute(
            """
            SELECT la.modality_ijson AS m
            FROM linguistic_analyses la
            JOIN text_units tu ON tu.text_unit_uuid = la.text_unit_uuid
            WHERE tu.message_uuid = %s
            """,
            (message_uuid,),
        ).fetchone()
        polarities = [
            str(dict(row)["polarity"])
            for row in connection.execute(
                """
                SELECT mc.polarity
                FROM memory_candidates mc
                JOIN text_units tu ON tu.text_unit_uuid = mc.text_unit_uuid
                WHERE tu.message_uuid = %s
                """,
                (message_uuid,),
            ).fetchall()
        ]
        version_polarities = [
            str(dict(row)["polarity"])
            for row in connection.execute(
                """
                SELECT mv.polarity
                FROM memory_versions mv
                JOIN memory_candidates mc ON mc.candidate_uuid = mv.source_candidate_uuid
                JOIN text_units tu ON tu.text_unit_uuid = mc.text_unit_uuid
                WHERE tu.message_uuid = %s
                """,
                (message_uuid,),
            ).fetchall()
        ]

    assert modality is not None
    payload = json.loads(str(dict(modality)["m"]))
    assert payload["polarity"] == Polarity.NEGATED.value
    assert payload["semantic_authority"] == NON_AUTHORITATIVE_LEXICAL_HINT
    assert polarities, "the fixture must produce at least one candidate"
    assert set(polarities) == {Polarity.UNKNOWN.value}
    assert version_polarities, "the fixture must produce at least one memory version"
    assert set(version_polarities) == {Polarity.UNKNOWN.value}


def _seed_message(connection: Any, text: str) -> str:
    suffix = uuid4().hex
    workspace_uuid, project_uuid = str(uuid4()), str(uuid4())
    session_uuid, message_uuid = str(uuid4()), str(uuid4())
    now = utc_now()
    connection.execute(
        "INSERT INTO workspaces (workspace_uuid, name, created_at, updated_at, schema_version)"
        " VALUES (?, ?, ?, ?, 1)",
        (workspace_uuid, f"workspace {suffix}", now, now),
    )
    connection.execute(
        "INSERT INTO projects (project_uuid, workspace_uuid, name, created_at, updated_at,"
        " schema_version) VALUES (?, ?, ?, ?, ?, 1)",
        (project_uuid, workspace_uuid, f"project {suffix}", now, now),
    )
    connection.execute(
        "INSERT INTO sessions (session_uuid, workspace_uuid, project_uuid, status, created_at,"
        " updated_at, schema_version) VALUES (?, ?, ?, 'active', ?, ?, 1)",
        (session_uuid, workspace_uuid, project_uuid, now, now),
    )
    connection.execute(
        "INSERT INTO messages (message_uuid, session_uuid, turn_index, role, creator_type,"
        " raw_text, processing_status, visibility, is_deleted, created_at, updated_at,"
        " schema_version) VALUES (?, ?, 0, 'user', 'user', ?, 'pending', 'visible', false,"
        " ?, ?, 1)",
        (message_uuid, session_uuid, text, now, now),
    )
    connection.commit()
    return message_uuid
