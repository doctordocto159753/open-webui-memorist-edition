from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from memcore.api.routes_openwebui import _pg_enqueue_capture_jobs
from memcore.config import Settings
from memcore.imports.runtime import import_connection, initialize_runtime_storage
from memcore.memory_worker.postgres.pipeline import PostgresMemoryWorkerPipeline
from memcore.memory_worker.prompts.contracts import canonical_jakobson_v3_example
from memcore.model_control.postgres_repository import PostgresModelControlRepository
from memcore.model_control.providers.base import ProviderHealth
from memcore.model_control.role_contracts import role_contract_manifest_hash
from memcore.model_control.schemas import ModelProfileCreate, ProviderType
from memcore.model_control.stage_contracts import (
    deterministic_privacy,
    validate_privacy_result,
)
from memcore.model_control.stage_invocation import (
    StageInvocationRequest,
    StageInvoker,
)
from memcore.models import ModelRole, utc_now
from memcore.validators.ijson import canonical_hash_ijson

pytestmark = [
    pytest.mark.skipif(
        not os.getenv("MEMORIST_POSTGRES_DSN"),
        reason="requires real PostgreSQL",
    ),
    pytest.mark.usefixtures("wp02_downstream_semantic_model"),
]

MODEL_TARGET: dict[str, Any] = {
    "model_role": ModelRole.MEMORY_EXTRACTION.value,
    "provider_type": ProviderType.DETERMINISTIC.value,
    "model_name": "deterministic_extraction",
}


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        runtime_profile="full",
        canonical_store="postgres",
        postgres_dsn=os.environ["MEMORIST_POSTGRES_DSN"],
        object_store_path=str(tmp_path / "objects"),
        db_path=str(tmp_path / "unused.sqlite3"),
        graph_backend="disabled",
        allow_full_graph_degraded=True,
        hot_scheduler="in_memory",
    )


def _create_message(connection: Any, text: str) -> str:
    suffix = uuid4().hex
    workspace_uuid = str(uuid4())
    project_uuid = str(uuid4())
    session_uuid = str(uuid4())
    message_uuid = str(uuid4())
    now = utc_now()
    connection.execute(
        """
        INSERT INTO workspaces (
            workspace_uuid, name, created_at, updated_at, schema_version
        ) VALUES (?, ?, ?, ?, 1)
        """,
        (workspace_uuid, f"workspace {suffix}", now, now),
    )
    connection.execute(
        """
        INSERT INTO projects (
            project_uuid, workspace_uuid, name, created_at, updated_at, schema_version
        ) VALUES (?, ?, ?, ?, ?, 1)
        """,
        (project_uuid, workspace_uuid, f"project {suffix}", now, now),
    )
    connection.execute(
        """
        INSERT INTO sessions (
            session_uuid, workspace_uuid, project_uuid, status,
            created_at, updated_at, schema_version
        ) VALUES (?, ?, ?, 'active', ?, ?, 1)
        """,
        (session_uuid, workspace_uuid, project_uuid, now, now),
    )
    connection.execute(
        """
        INSERT INTO messages (
            message_uuid, session_uuid, turn_index, role, creator_type, raw_text,
            processing_status, visibility, is_deleted, created_at, updated_at,
            schema_version
        ) VALUES (?, ?, 0, 'user', 'user', ?, 'pending', 'visible', false, ?, ?, 1)
        """,
        (message_uuid, session_uuid, text, now, now),
    )
    connection.commit()
    return message_uuid


def _create_deterministic_profile(
    connection: Any,
    role: ModelRole,
    name: str,
) -> str:
    profile = PostgresModelControlRepository(connection).create_profile(
        ModelProfileCreate(
            profile_name=name,
            provider_type=ProviderType.DETERMINISTIC,
            provider_name="test deterministic",
            model_name=name,
            role=role,
            endpoint_is_local=True,
            supports_structured_output=True,
            supports_json_mode=True,
            supports_embeddings=role is ModelRole.EMBEDDING,
            embedding_dimension=16 if role is ModelRole.EMBEDDING else None,
            privacy_acknowledged=True,
        )
    )
    PostgresModelControlRepository(connection).set_default(
        role,
        profile.model_profile_uuid,
    )
    connection.commit()
    return profile.model_profile_uuid


class _MemoryProviderHandler(BaseHTTPRequestHandler):
    calls = 0

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        type(self).calls += 1
        output = canonical_jakobson_v3_example()
        if type(self).calls == 1:
            output["status"] = "done"
        else:
            input_payload = json.loads(payload["messages"][1]["content"])
            output["items"][0]["text"] = input_payload["sentences"][0]["text"]
        body = json.dumps(
            {
                "id": f"pg-attempt-{type(self).calls}",
                "choices": [{"message": {"content": json.dumps(output)}}],
                "usage": {"prompt_tokens": 9, "completion_tokens": 5},
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        return


def test_full_postgres_capture_schedules_remote_jsonb_profile(
    tmp_path: Path,
) -> None:
    """Regression for F1: never decode PostgreSQL JSONB with the SQLite mapper."""

    settings = _settings(tmp_path)
    initialize_runtime_storage(settings)
    with import_connection(settings) as connection:
        connection.execute("DELETE FROM model_role_defaults WHERE role = 'memory_extraction'")
        message_uuid = _create_message(
            connection,
            "Remember that Full mode keeps model control in PostgreSQL.",
        )
        session_uuid = str(
            connection.execute(
                "SELECT session_uuid FROM messages WHERE message_uuid = ?",
                (message_uuid,),
            ).fetchone()["session_uuid"]
        )
        repository = PostgresModelControlRepository(connection)
        profile = repository.create_profile(
            ModelProfileCreate(
                profile_name="F1 remote capture regression",
                provider_type=ProviderType.OPENAI_COMPATIBLE_LLM,
                provider_name="controlled-test-provider",
                model_name="controlled-memory-model",
                role=ModelRole.MEMORY_EXTRACTION,
                endpoint_url="http://127.0.0.1:9/custom/v1",
                endpoint_is_local=True,
                supports_structured_output=True,
                supports_json_mode=True,
                privacy_acknowledged=True,
            )
        )
        repository.record_health_event(
            profile.model_profile_uuid,
            ProviderHealth(
                status="ok",
                provider_type=profile.provider_type,
                model_name=profile.model_name,
                latency_ms=1,
                local_only_safe=True,
                authentication_status="not_required",
                role_compatibility_status="compatible",
                overall_status="ok",
                schema_mode="json_schema",
                role_manifest_hash=role_contract_manifest_hash(profile.role),
                role_probe_status="compatible",
            ),
        )
        repository.set_default(ModelRole.MEMORY_EXTRACTION, profile.model_profile_uuid)

        _pg_enqueue_capture_jobs(
            connection.raw,
            session_uuid=session_uuid,
            message_uuid=message_uuid,
            now=utc_now(),
            settings=settings,
        )
        queued = connection.execute(
            """
            SELECT payload_jsonb
            FROM jobs
            WHERE job_type = 'memory_extraction'
              AND idempotency_key = ?
            """,
            (f"openwebui:memory_extraction:{message_uuid}",),
        ).fetchone()["payload_jsonb"]
        usage = connection.execute(
            """
            SELECT model_profile_uuid, provider_type, model_name
            FROM model_usage_events
            WHERE message_uuid = ? AND event_type = 'queued'
            """,
            (message_uuid,),
        ).fetchone()
        connection.execute(
            """
            DELETE FROM model_role_defaults
            WHERE role = 'memory_extraction' AND model_profile_uuid = ?
            """,
            (profile.model_profile_uuid,),
        )
        connection.commit()

    assert queued["model_profile_uuid"] == profile.model_profile_uuid
    assert queued["provider_type"] == ProviderType.OPENAI_COMPATIBLE_LLM.value
    assert queued["model_name"] == "controlled-memory-model"
    assert usage["model_profile_uuid"] == profile.model_profile_uuid
    assert usage["provider_type"] == ProviderType.OPENAI_COMPATIBLE_LLM.value
    assert usage["model_name"] == "controlled-memory-model"


def test_full_postgres_stage_replay_returns_typed_stored_output_after_restart(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    initialize_runtime_storage(settings)
    identity = f"pg-stage-replay-{uuid4().hex}"
    request = StageInvocationRequest(
        role=ModelRole.PRIVACY_SENSITIVITY,
        stage="privacy_sensitivity",
        source_type="memory_candidate",
        source_uuid=f"candidate-{uuid4().hex}",
        prompt_id="memorist.privacy_sensitivity",
        prompt_version="2.0",
        idempotency_key=identity,
        input_payload={
            "candidate_text": "Always retain the PostgreSQL release checklist.",
            "evidence_text": "Always retain the PostgreSQL release checklist.",
        },
    )
    with import_connection(settings) as connection:
        _create_deterministic_profile(
            connection,
            ModelRole.PRIVACY_SENSITIVITY,
            f"privacy-{uuid4().hex}",
        )
        first = StageInvoker(
            connection,
            PostgresModelControlRepository(connection),
            postgres=True,
        ).invoke_structured(
            request,
            validator=validate_privacy_result,
            deterministic_output=deterministic_privacy,
        )

    with import_connection(settings) as restarted:
        replay = StageInvoker(
            restarted,
            PostgresModelControlRepository(restarted),
            postgres=True,
        ).invoke_structured(
            request,
            validator=validate_privacy_result,
            deterministic_output=deterministic_privacy,
        )
        stored = restarted.execute(
            """
            SELECT output_jsonb, output_hash
            FROM processing_stage_runs
            WHERE idempotency_key = ?
            """,
            (identity,),
        ).fetchone()
        copies = restarted.execute(
            "SELECT COUNT(*) FROM processing_stage_runs WHERE idempotency_key = ?",
            (identity,),
        ).fetchone()[0]

    assert first.output is not None
    assert replay.idempotent_replay is True
    assert replay.execution_uuid == first.execution_uuid
    assert replay.output == first.output
    assert stored["output_jsonb"] == first.output
    assert stored["output_hash"] == canonical_hash_ijson(first.output)
    assert copies == 1


def test_full_postgres_candidate_stage_replay_keeps_status_and_memory_stable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path)
    initialize_runtime_storage(settings)
    with import_connection(settings) as connection:
        message_uuid = _create_message(
            connection,
            "Do not disable PostgreSQL backups.",
        )
        pipeline = PostgresMemoryWorkerPipeline(connection, settings)
        prepared = pipeline.prepare_message(message_uuid, MODEL_TARGET)

        original_embeddings = PostgresMemoryWorkerPipeline._run_embedding_stages

        def crash_after_candidate_and_memory_persistence(
            *_args: object,
            **_kwargs: object,
        ) -> list[dict[str, Any]]:
            raise RuntimeError("injected crash after candidate and memory persistence")

        monkeypatch.setattr(
            PostgresMemoryWorkerPipeline,
            "_run_embedding_stages",
            crash_after_candidate_and_memory_persistence,
        )
        with pytest.raises(RuntimeError, match="injected crash"):
            pipeline.process_message(
                message_uuid,
                model_target=MODEL_TARGET,
                prepared_inference=prepared,
            )
        first_candidates = [
            (str(row["candidate_uuid"]), str(row["status"]))
            for row in connection.execute(
                """
                SELECT mc.candidate_uuid, mc.status
                FROM memory_candidates mc
                JOIN text_units tu ON tu.text_unit_uuid = mc.text_unit_uuid
                WHERE tu.message_uuid = ?
                ORDER BY mc.candidate_uuid
                """,
                (message_uuid,),
            ).fetchall()
        ]
        first_memories = int(
            connection.execute(
                """
                SELECT COUNT(*)
                FROM memory_versions mv
                JOIN memory_candidates mc
                  ON mc.candidate_uuid = mv.source_candidate_uuid
                JOIN text_units tu ON tu.text_unit_uuid = mc.text_unit_uuid
                WHERE tu.message_uuid = ?
                """,
                (message_uuid,),
            ).fetchone()[0]
        )
        first_stages = [
            (
                str(row["source_uuid"]),
                str(row["stage"]),
                row["output_jsonb"],
                str(row["output_hash"]),
            )
            for row in connection.execute(
                """
                SELECT psr.source_uuid, psr.stage, psr.output_jsonb, psr.output_hash
                FROM processing_stage_runs psr
                WHERE psr.source_uuid IN (
                    SELECT mc.candidate_uuid
                    FROM memory_candidates mc
                    JOIN text_units tu ON tu.text_unit_uuid = mc.text_unit_uuid
                    WHERE tu.message_uuid = ?
                )
                ORDER BY psr.source_uuid, psr.stage
                """,
                (message_uuid,),
            ).fetchall()
        ]
        assert first_candidates
        assert first_memories > 0
        assert {row[1] for row in first_stages} >= {"privacy_sensitivity"}

    monkeypatch.setattr(
        PostgresMemoryWorkerPipeline,
        "_run_embedding_stages",
        original_embeddings,
    )
    with import_connection(settings) as restarted:
        PostgresMemoryWorkerPipeline(restarted, settings).process_message(
            message_uuid,
            model_target=MODEL_TARGET,
        )
        replay_candidates = [
            (str(row["candidate_uuid"]), str(row["status"]))
            for row in restarted.execute(
                """
                SELECT mc.candidate_uuid, mc.status
                FROM memory_candidates mc
                JOIN text_units tu ON tu.text_unit_uuid = mc.text_unit_uuid
                WHERE tu.message_uuid = ?
                ORDER BY mc.candidate_uuid
                """,
                (message_uuid,),
            ).fetchall()
        ]
        replay_memories = int(
            restarted.execute(
                """
                SELECT COUNT(*)
                FROM memory_versions mv
                JOIN memory_candidates mc
                  ON mc.candidate_uuid = mv.source_candidate_uuid
                JOIN text_units tu ON tu.text_unit_uuid = mc.text_unit_uuid
                WHERE tu.message_uuid = ?
                """,
                (message_uuid,),
            ).fetchone()[0]
        )
        replay_stages = [
            (
                str(row["source_uuid"]),
                str(row["stage"]),
                row["output_jsonb"],
                str(row["output_hash"]),
            )
            for row in restarted.execute(
                """
                SELECT psr.source_uuid, psr.stage, psr.output_jsonb, psr.output_hash
                FROM processing_stage_runs psr
                WHERE psr.source_uuid IN (
                    SELECT mc.candidate_uuid
                    FROM memory_candidates mc
                    JOIN text_units tu ON tu.text_unit_uuid = mc.text_unit_uuid
                    WHERE tu.message_uuid = ?
                )
                ORDER BY psr.source_uuid, psr.stage
                """,
                (message_uuid,),
            ).fetchall()
        ]

    assert replay_candidates == first_candidates
    assert replay_memories == first_memories
    assert replay_stages == first_stages


def test_full_postgres_remote_attempt_audit_and_final_stage_parity(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    initialize_runtime_storage(settings)
    _MemoryProviderHandler.calls = 0
    server = ThreadingHTTPServer(("127.0.0.1", 0), _MemoryProviderHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    profile: dict[str, Any] = {
        "model_role": "memory_extraction",
        "requested_role": "memory_extraction",
        "effective_role": "memory_extraction",
        "scope_source": "explicit_override",
        "provider_type": "openai_compatible_llm",
        "model_name": "controlled-pg-provider",
        "endpoint_url": f"http://127.0.0.1:{server.server_port}/v1",
        "supports_json_mode": True,
        "supports_structured_output": False,
    }
    try:
        with import_connection(settings) as connection:
            message_uuid = _create_message(connection, "Keep PostgreSQL backups enabled.")
            pipeline = PostgresMemoryWorkerPipeline(connection, settings)
            prepared = pipeline.prepare_message(
                message_uuid,
                profile,
                job_uuid="pg-provider-audit-job",
            )
            attempts = connection.execute(
                "SELECT * FROM processing_provider_attempts "
                "WHERE processing_run_uuid = ? ORDER BY attempt_index",
                (prepared.processing_run_uuid,),
            ).fetchall()
            assert len(attempts) == 2
            assert attempts[0]["schema_valid"] is False
            assert attempts[1]["schema_valid"] is True
            assert all(row["completed_at"] is not None for row in attempts)
            pipeline.process_message(
                message_uuid,
                model_target=profile,
                prepared_inference=prepared,
            )
            stage = connection.execute(
                "SELECT * FROM processing_stage_runs "
                "WHERE processing_run_uuid = ? AND stage = 'jakobson_sentence_analysis'",
                (prepared.processing_run_uuid,),
            ).fetchone()
            assert stage["status"] == "ok"
            assert stage["attempt_count"] == 2
            assert stage["contract_hash"] == prepared.contract_hash
            assert stage["scope_source"] == "explicit_override"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_full_postgres_embedding_projection_recovers_after_stage_persisted_crash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path)
    initialize_runtime_storage(settings)
    with import_connection(settings) as connection:
        profile_uuid = _create_deterministic_profile(
            connection,
            ModelRole.EMBEDDING,
            f"embedding-{uuid4().hex}",
        )
        message_uuid = _create_message(
            connection,
            "Remember this: I prefer deterministic PostgreSQL embedding recovery.",
        )
        original_invoke = StageInvoker.invoke_embedding
        crashed = False

        def persist_stage_then_crash(
            self: StageInvoker,
            request: StageInvocationRequest,
            *,
            texts: list[str],
            expected_dimension: int | None = None,
            allow_replay: bool = True,
            lease_fence: Any = None,
        ) -> tuple[Any, list[list[float]]]:
            nonlocal crashed
            result = original_invoke(
                self,
                request,
                texts=texts,
                expected_dimension=expected_dimension,
                allow_replay=allow_replay,
                lease_fence=lease_fence,
            )
            if not crashed:
                crashed = True
                raise RuntimeError("injected crash after embedding stage persistence")
            return result

        monkeypatch.setattr(StageInvoker, "invoke_embedding", persist_stage_then_crash)
        with pytest.raises(RuntimeError, match="injected crash"):
            PostgresMemoryWorkerPipeline(connection, settings).process_message(
                message_uuid,
                model_target=MODEL_TARGET,
            )
        version_uuid = str(
            connection.execute(
                """
                SELECT mv.memory_version_uuid
                FROM memory_versions mv
                JOIN memory_candidates mc
                  ON mc.candidate_uuid = mv.source_candidate_uuid
                JOIN text_units tu ON tu.text_unit_uuid = mc.text_unit_uuid
                WHERE tu.message_uuid = ?
                """,
                (message_uuid,),
            ).fetchone()["memory_version_uuid"]
        )
        assert (
            connection.execute(
                """
                SELECT COUNT(*)
                FROM processing_stage_runs
                WHERE stage = 'embedding_generation' AND source_uuid = ?
                """,
                (version_uuid,),
            ).fetchone()[0]
            == 1
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM memory_version_embeddings WHERE memory_version_uuid = ?",
                (version_uuid,),
            ).fetchone()[0]
            == 0
        )

    monkeypatch.setattr(StageInvoker, "invoke_embedding", original_invoke)
    with import_connection(settings) as restarted:
        PostgresMemoryWorkerPipeline(restarted, settings).process_message(
            message_uuid,
            model_target=MODEL_TARGET,
        )
        projection = restarted.execute(
            """
            SELECT embedding_model, embedding_dimension
            FROM memory_version_embeddings
            WHERE memory_version_uuid = ?
            """,
            (version_uuid,),
        ).fetchall()
        outbox = restarted.execute(
            """
            SELECT status, attempts
            FROM embedding_outbox
            WHERE event_type = 'memory_version_upserted' AND source_uuid = ?
            """,
            (version_uuid,),
        ).fetchone()
        stage_count = restarted.execute(
            """
            SELECT COUNT(*)
            FROM processing_stage_runs
            WHERE stage = 'embedding_generation' AND source_uuid = ?
            """,
            (version_uuid,),
        ).fetchone()[0]

    assert len(projection) == 1
    assert projection[0]["embedding_model"].startswith("embedding-")
    assert int(projection[0]["embedding_dimension"]) == 16
    assert dict(outbox) == {"status": "succeeded", "attempts": 2}
    # Each projection attempt has a distinct audited input identity while the
    # canonical projection remains a single upserted row.
    assert stage_count == 2
    assert profile_uuid
