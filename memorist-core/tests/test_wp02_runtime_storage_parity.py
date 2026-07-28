from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from memcore.config import Settings
from memcore.imports.runtime import import_connection, initialize_runtime_storage
from memcore.memory_worker.pipeline import MemoryWorkerPipeline
from memcore.memory_worker.postgres.pipeline import PostgresMemoryWorkerPipeline
from memcore.memory_worker.providers.openai_compatible import (
    OpenAICompatibleMemoryExtractionProvider,
)
from memcore.models import utc_now
from memcore.storage.migrations import apply_migrations
from memcore.storage.sqlite import connect
from test_wp02_runtime_lite import _BundleProvider, _profile, _seed_trusted_message


@pytest.mark.skipif(not os.getenv("MEMORIST_POSTGRES_DSN"), reason="requires real PostgreSQL")
def test_lite_and_full_make_same_storage_backed_semantic_decision_and_replay(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    dsn = os.environ["MEMORIST_POSTGRES_DSN"]
    profile = _profile()
    active_provider: list[_BundleProvider] = [_BundleProvider()]
    monkeypatch.setattr(
        OpenAICompatibleMemoryExtractionProvider,
        "from_profile",
        classmethod(lambda cls, value, timeout_ms=8000: active_provider[0]),
    )

    lite_connection = connect(tmp_path / "parity-lite.sqlite")
    apply_migrations(lite_connection)
    try:
        lite_message = _seed_trusted_message(lite_connection)
        lite_pipeline = MemoryWorkerPipeline(
            lite_connection,
            Settings(
                db_path=str(tmp_path / "parity-lite.sqlite"),
                object_store_path=str(tmp_path / "lite-objects"),
                graph_backend="disabled",
            ),
        )
        lite_result = lite_pipeline.process_message(lite_message, model_target=profile)
        lite_projection = _lite_projection(lite_connection, lite_message)
        lite_contracts = list(active_provider[0].schema_names)
    finally:
        lite_connection.close()

    full_provider = _BundleProvider()
    active_provider[0] = full_provider
    full_settings = Settings(
        env="test",
        runtime_profile="full",
        canonical_store="postgres",
        postgres_dsn=dsn,
        object_store_path=str(tmp_path / "full-objects"),
        db_path=str(tmp_path / "unused.sqlite"),
        graph_backend="disabled",
        allow_full_graph_degraded=True,
        hot_scheduler="in_memory",
    )
    initialize_runtime_storage(full_settings)
    with import_connection(full_settings) as full_connection:
        full_message = _seed_full_trusted_message(full_connection)
        full_pipeline = PostgresMemoryWorkerPipeline(full_connection, full_settings)
        full_result = full_pipeline.process_message(full_message, model_target=profile)
        full_projection = _full_projection(full_connection, full_message)
        calls_before_restart = len(full_provider.schema_names)
        replay = full_pipeline.process_message(full_message, model_target=profile)
        full_connection.commit()
        candidate_count = int(
            full_connection.execute(
                """
                SELECT COUNT(*) AS count
                FROM memory_candidates candidate
                JOIN text_units unit
                  ON unit.text_unit_uuid = candidate.text_unit_uuid
                WHERE unit.message_uuid = %s
                """,
                (full_message,),
            ).fetchone()["count"]
        )

    assert (
        lite_contracts
        == full_provider.schema_names
        == [
            "memorist_jakobson_sentence_analysis_v3",
            "memorist_semantic_candidate_analysis_v1",
        ]
    )
    assert lite_result["semantic_coverage_status"] == full_result["semantic_coverage_status"]
    assert lite_result["semantic_proposals"] == full_result["semantic_proposals"] == 1
    assert lite_projection == full_projection
    assert replay["idempotent_replay"] is True
    assert len(full_provider.schema_names) == calls_before_restart
    assert candidate_count == 1


def _lite_projection(connection: Any, message_uuid: str) -> dict[str, Any]:
    output = connection.execute(
        """
        SELECT validated_output_ijson
        FROM prompt_execution_runs
        WHERE message_uuid = ?
          AND prompt_id = 'memorist.semantic_candidate_analysis'
        """,
        (message_uuid,),
    ).fetchone()
    candidate = connection.execute(
        """
        SELECT candidate.candidate_type, candidate.subject_key, candidate.predicate,
               candidate.object_ijson, candidate.normalized_text,
               candidate.source_authority, candidate.explicitness,
               candidate.polarity, candidate.status
        FROM memory_candidates candidate
        JOIN text_units unit ON unit.text_unit_uuid = candidate.text_unit_uuid
        WHERE unit.message_uuid = ?
        """,
        (message_uuid,),
    ).fetchone()
    coverage = connection.execute(
        """
        SELECT item.semantic_unit_id, item.raw_start, item.raw_end,
               item.disposition, item.reason_codes_ijson
        FROM semantic_coverage_items item
        JOIN semantic_coverage_runs run
          ON run.coverage_run_uuid = item.coverage_run_uuid
        WHERE run.message_uuid = ?
        ORDER BY item.raw_start, item.raw_end
        """,
        (message_uuid,),
    ).fetchall()
    route_gate = connection.execute(
        """
        SELECT route.route_type, route.status AS route_status,
               gate.decision AS gate_decision
        FROM text_units unit
        JOIN memory_gate_decisions gate ON gate.text_unit_uuid = unit.text_unit_uuid
        JOIN memory_signal_routes route ON route.unit_uuid = unit.text_unit_uuid
        WHERE unit.message_uuid = ?
        ORDER BY route.priority DESC
        LIMIT 1
        """,
        (message_uuid,),
    ).fetchone()
    return _projection(output, candidate, coverage, route_gate)


def _full_projection(connection: Any, message_uuid: str) -> dict[str, Any]:
    output = connection.execute(
        """
        SELECT validated_output_ijson
        FROM prompt_execution_runs
        WHERE message_uuid = %s
          AND prompt_id = 'memorist.semantic_candidate_analysis'
        """,
        (message_uuid,),
    ).fetchone()
    candidate = connection.execute(
        """
        SELECT candidate.candidate_type, candidate.subject_key, candidate.predicate,
               candidate.object_jsonb AS object_ijson, candidate.normalized_text,
               candidate.source_authority, candidate.explicitness,
               candidate.polarity, candidate.status
        FROM memory_candidates candidate
        JOIN text_units unit ON unit.text_unit_uuid = candidate.text_unit_uuid
        WHERE unit.message_uuid = %s
        """,
        (message_uuid,),
    ).fetchone()
    coverage = connection.execute(
        """
        SELECT item.semantic_unit_id, item.raw_start, item.raw_end,
               item.disposition, item.reason_codes_jsonb AS reason_codes_ijson
        FROM semantic_coverage_items item
        JOIN semantic_coverage_runs run
          ON run.coverage_run_uuid = item.coverage_run_uuid
        WHERE run.message_uuid = %s
        ORDER BY item.raw_start, item.raw_end
        """,
        (message_uuid,),
    ).fetchall()
    route_gate = connection.execute(
        """
        SELECT route.route_type, route.status AS route_status,
               gate.decision AS gate_decision
        FROM text_units unit
        JOIN memory_gate_decisions gate ON gate.text_unit_uuid = unit.text_unit_uuid
        JOIN memory_signal_routes route ON route.unit_uuid = unit.text_unit_uuid
        WHERE unit.message_uuid = %s
        ORDER BY route.priority DESC
        LIMIT 1
        """,
        (message_uuid,),
    ).fetchone()
    return _projection(output, candidate, coverage, route_gate)


def _projection(
    output: Any,
    candidate: Any,
    coverage: Any,
    route_gate: Any,
) -> dict[str, Any]:
    assert output is not None and candidate is not None and route_gate is not None
    semantic = output["validated_output_ijson"]
    if isinstance(semantic, str):
        semantic = json.loads(semantic)
    object_value = candidate["object_ijson"]
    if isinstance(object_value, str):
        object_value = json.loads(object_value)
    status = str(candidate["status"])
    if status == "accepted":
        status = "ready_for_consolidation"
    return {
        "semantic_output": semantic,
        "candidate": {
            "candidate_type": str(candidate["candidate_type"]),
            "subject_key": str(candidate["subject_key"]),
            "predicate": str(candidate["predicate"]),
            "object": object_value,
            "normalized_text": str(candidate["normalized_text"]),
            "source_authority": str(candidate["source_authority"]),
            "explicitness": str(candidate["explicitness"]),
            "polarity": str(candidate["polarity"]),
            "status": status,
        },
        "coverage": [
            (
                str(row["semantic_unit_id"]) if row["semantic_unit_id"] is not None else None,
                int(row["raw_start"]),
                int(row["raw_end"]),
                str(row["disposition"]),
                _json_list(row["reason_codes_ijson"]),
            )
            for row in coverage
        ],
        "authority": (
            str(route_gate["route_type"]),
            str(route_gate["route_status"]),
            str(route_gate["gate_decision"]),
        ),
    }


def _json_list(value: Any) -> list[str]:
    parsed = json.loads(value) if isinstance(value, str) else value
    return [str(item) for item in parsed]


def _seed_full_trusted_message(connection: Any) -> str:
    workspace_uuid = str(uuid4())
    project_uuid = str(uuid4())
    session_uuid = str(uuid4())
    message_uuid = str(uuid4())
    version_uuid = str(uuid4())
    now = utc_now()
    text = "I prefer concise answers."
    connection.execute(
        """
        INSERT INTO workspaces (
          workspace_uuid, name, created_at, updated_at, schema_version
        ) VALUES (%s, %s, %s, %s, 1)
        """,
        (workspace_uuid, "WP02 parity workspace", now, now),
    )
    connection.execute(
        """
        INSERT INTO projects (
          project_uuid, workspace_uuid, name, created_at, updated_at, schema_version
        ) VALUES (%s, %s, %s, %s, %s, 1)
        """,
        (project_uuid, workspace_uuid, "WP02 parity project", now, now),
    )
    connection.execute(
        """
        INSERT INTO sessions (
          session_uuid, workspace_uuid, project_uuid, status,
          created_at, updated_at, schema_version
        ) VALUES (%s, %s, %s, 'active', %s, %s, 1)
        """,
        (session_uuid, workspace_uuid, project_uuid, now, now),
    )
    connection.execute(
        """
        INSERT INTO memorist_session_actors (
          session_uuid, user_uuid, workspace_uuid, created_at, schema_version
        ) VALUES (%s, %s, %s, %s, 1)
        """,
        (session_uuid, "user-1", workspace_uuid, now),
    )
    connection.execute(
        """
        INSERT INTO messages (
          message_uuid, session_uuid, turn_index, role, creator_type, raw_text,
          processing_status, visibility, is_deleted, redaction_status,
          created_at, updated_at, schema_version
        ) VALUES (
          %s, %s, 0, 'user', 'user', %s, 'pending', 'visible', false, 'none',
          %s, %s, 1
        )
        """,
        (message_uuid, session_uuid, text, now, now),
    )
    connection.execute(
        """
        INSERT INTO message_versions (
          message_version_uuid, message_uuid, version_number, raw_text,
          created_by, created_at, schema_version
        ) VALUES (%s, %s, 1, %s, %s, %s, 1)
        """,
        (version_uuid, message_uuid, text, "user-1", now),
    )
    connection.commit()
    return message_uuid
