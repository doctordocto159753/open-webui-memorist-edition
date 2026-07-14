from __future__ import annotations

import copy
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from memcore.config import Settings, get_settings
from memcore.imports.runtime import initialize_runtime_storage
from memcore.main import create_app
from memcore.memory_control import memory_control_connection
from memcore.memory_worker.pipeline import MemoryWorkerPipeline
from memcore.memory_worker.postgres.pipeline import PostgresMemoryWorkerPipeline
from memcore.memory_worker.prepared import PreparedJakobsonInference

RUNTIME = "full" if __import__("os").getenv("MEMORIST_SEMANTIC_BASELINE_RUNTIME") == "full" else "lite"
MODEL_TARGET: dict[str, object] = {
    "model_role": "memory_extraction",
    "provider_type": "deterministic",
    "model_name": "deterministic_extraction",
}


@dataclass(frozen=True)
class SemanticSnapshot:
    dominant_functions: list[str]
    routes: list[str]
    gate_decisions: list[str]
    candidates: list[str]
    memory_count: int


@pytest.fixture
def runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, Settings]:
    monkeypatch.setenv("MEMORIST_ENV", "test")
    monkeypatch.setenv("MEMORIST_ALLOW_LEGACY_ACTOR_HEADERS_FOR_TESTS", "true")
    monkeypatch.setenv("MEMORIST_OBJECT_STORE_PATH", str(tmp_path / "objects"))
    if RUNTIME == "full":
        dsn = __import__("os").getenv("MEMORIST_POSTGRES_DSN")
        if not dsn:
            pytest.fail("Full semantic baseline characterization requires PostgreSQL")
        monkeypatch.setenv("MEMORIST_RUNTIME_PROFILE", "full")
        monkeypatch.setenv("MEMORIST_CANONICAL_STORE", "postgres")
        monkeypatch.setenv("MEMORIST_POSTGRES_DSN", dsn)
        monkeypatch.setenv("MEMORIST_GRAPH_BACKEND", "disabled")
        monkeypatch.setenv("MEMORIST_ALLOW_FULL_GRAPH_DEGRADED", "true")
        monkeypatch.setenv("MEMORIST_HOT_SCHEDULER", "in_memory")
    else:
        monkeypatch.setenv("MEMORIST_RUNTIME_PROFILE", "lite")
        monkeypatch.setenv("MEMORIST_CANONICAL_STORE", "sqlite")
        monkeypatch.setenv("MEMORIST_DB_PATH", str(tmp_path / "semantic-baseline.sqlite3"))
        monkeypatch.setenv("MEMORIST_GRAPH_BACKEND", "disabled")
        monkeypatch.setenv("MEMORIST_HOT_SCHEDULER", "disabled")
    get_settings.cache_clear()
    settings = get_settings()
    initialize_runtime_storage(settings)
    return TestClient(create_app()), settings


def _headers(user: str, workspace: str = "semantic-workspace") -> dict[str, str]:
    return {
        "X-Memorist-User-Id": user,
        "X-Memorist-Workspace-Id": workspace,
    }


def _capture_user_message(client: TestClient, *, content: str, conversation: str) -> tuple[str, str]:
    user = f"semantic-user-{conversation}"
    session = client.post(
        "/memcore/openwebui/session/resolve",
        json={
            "openwebui_conversation_id": conversation,
            "user_id": user,
            "workspace_uuid": "semantic-workspace",
            "turn_policy": "no_recall",
        },
        headers=_headers(user),
    )
    assert session.status_code == 200, session.text
    session_payload = session.json()
    captured = client.post(
        "/memcore/openwebui/messages/capture",
        json={
            "session_uuid": session_payload["session_uuid"],
            "openwebui_conversation_id": conversation,
            "user_id": user,
            "workspace_uuid": session_payload["workspace_uuid"],
            "role": "user",
            "content": content,
            "idempotency_key": f"capture-{conversation}",
            "turn_policy": "no_recall",
        },
        headers=_headers(user, str(session_payload["workspace_uuid"])),
    )
    assert captured.status_code == 200, captured.text
    return str(captured.json()["message_uuid"]), str(session_payload["workspace_uuid"])


def _run_pipeline(settings: Settings, message_uuid: str, *, force_phatic: bool) -> None:
    with memory_control_connection(settings) as connection:
        pipeline: MemoryWorkerPipeline | PostgresMemoryWorkerPipeline
        if RUNTIME == "full":
            pipeline = PostgresMemoryWorkerPipeline(connection, settings)
        else:
            pipeline = MemoryWorkerPipeline(connection, settings)
        prepared: PreparedJakobsonInference | None = None
        if force_phatic:
            prepared = _force_prepared_phatic(pipeline.prepare_message(message_uuid, MODEL_TARGET))
        pipeline.process_message(
            message_uuid,
            model_target=MODEL_TARGET,
            prepared_inference=prepared,
        )
        connection.commit()


def _force_prepared_phatic(prepared: PreparedJakobsonInference) -> PreparedJakobsonInference:
    output = copy.deepcopy(prepared.output)
    sentence = output["sentences"][0]
    sentence["dominant_function"] = "phatic"
    sentence["secondary_functions"] = []
    sentence["function_reason"] = "Characterization fixture forces phatic output."
    output["overall_summary"]["dominant_overall_function"] = "phatic"
    output["overall_summary"]["secondary_overall_functions"] = []
    return replace(prepared, output=output, output_tokens=1)


def _snapshot(settings: Settings, message_uuid: str) -> SemanticSnapshot:
    with memory_control_connection(settings) as connection:
        return SemanticSnapshot(
            dominant_functions=_values(
                connection,
                "SELECT dominant_function FROM jakobson_sentence_annotations "
                "WHERE message_uuid = ? ORDER BY sentence_index",
                (message_uuid,),
            ),
            routes=_values(
                connection,
                "SELECT route_type || ':' || status FROM memory_signal_routes "
                "WHERE message_uuid = ? ORDER BY route_type, status",
                (message_uuid,),
            ),
            gate_decisions=_values(
                connection,
                "SELECT gd.decision FROM memory_gate_decisions gd "
                "JOIN text_units tu ON tu.text_unit_uuid = gd.text_unit_uuid "
                "WHERE tu.message_uuid = ? ORDER BY tu.unit_index",
                (message_uuid,),
            ),
            candidates=_values(
                connection,
                "SELECT mc.candidate_type || ':' || mc.status FROM memory_candidates mc "
                "JOIN text_units tu ON tu.text_unit_uuid = mc.text_unit_uuid "
                "WHERE tu.message_uuid = ? ORDER BY mc.candidate_type, mc.status",
                (message_uuid,),
            ),
            memory_count=_scalar_int(
                connection,
                "SELECT count(*) FROM memories m "
                "JOIN memory_versions mv ON mv.memory_uuid = m.memory_uuid "
                "JOIN memory_candidates mc ON mc.candidate_uuid = mv.source_candidate_uuid "
                "JOIN text_units tu ON tu.text_unit_uuid = mc.text_unit_uuid "
                "WHERE tu.message_uuid = ?",
                (message_uuid,),
            ),
        )


def _values(connection: Any, sql: str, params: tuple[object, ...]) -> list[str]:
    return [str(row[0]) for row in connection.execute(sql, params).fetchall()]


def _scalar_int(connection: Any, sql: str, params: tuple[object, ...]) -> int:
    row = connection.execute(sql, params).fetchone()
    assert row is not None
    return int(row[0])


def test_current_greeting_baseline_differs_between_lite_and_full(
    runtime: tuple[TestClient, Settings],
) -> None:
    client, settings = runtime
    message_uuid, _workspace = _capture_user_message(
        client,
        content="سلام",
        conversation="current-greeting-baseline",
    )
    _run_pipeline(settings, message_uuid, force_phatic=False)
    snapshot = _snapshot(settings, message_uuid)
    if RUNTIME == "lite":
        assert snapshot == SemanticSnapshot(
            dominant_functions=["phatic"],
            routes=["ignore:ignored"],
            gate_decisions=["discard"],
            candidates=[],
            memory_count=0,
        )
    else:
        assert snapshot == SemanticSnapshot(
            dominant_functions=["referential"],
            routes=["process_fact:ready"],
            gate_decisions=["discard"],
            candidates=["observation:accepted"],
            memory_count=1,
        )


def test_forced_phatic_annotation_baseline_records_full_gate_but_still_creates_memory(
    runtime: tuple[TestClient, Settings],
) -> None:
    client, settings = runtime
    message_uuid, _workspace = _capture_user_message(
        client,
        content="سلام",
        conversation="forced-phatic-baseline",
    )
    _run_pipeline(settings, message_uuid, force_phatic=True)
    snapshot = _snapshot(settings, message_uuid)
    if RUNTIME == "lite":
        assert snapshot == SemanticSnapshot(
            dominant_functions=["phatic"],
            routes=["ignore:ignored"],
            gate_decisions=["discard"],
            candidates=[],
            memory_count=0,
        )
    else:
        assert snapshot == SemanticSnapshot(
            dominant_functions=["phatic"],
            routes=["process_fact:ready"],
            gate_decisions=["discard"],
            candidates=["observation:accepted"],
            memory_count=1,
        )
