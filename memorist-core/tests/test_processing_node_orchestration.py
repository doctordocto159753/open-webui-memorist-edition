from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from memcore.model_control.endpoint import (
    EndpointConfigurationError,
    normalize_openai_endpoint,
)
from memcore.model_control.providers.base import ProviderHealth
from memcore.model_control.repository import ModelControlRepository
from memcore.model_control.resolution import RoleResolutionService
from memcore.model_control.role_contracts import role_contract_manifest_hash
from memcore.model_control.schemas import (
    ModelProfileCreate,
    ModelProfilePatch,
    ProviderType,
)
from memcore.model_control.stage_contracts import (
    deterministic_privacy,
    validate_privacy_result,
)
from memcore.model_control.stage_invocation import StageInvocationRequest, StageInvoker
from memcore.models import ModelRole
from memcore.repositories import ProjectRepository, WorkspaceRepository
from memcore.storage.migrations import apply_migrations
from memcore.storage.sqlite import connect
from memcore.validators.ijson import canonical_hash_ijson, dump_ijson, load_ijson


@pytest.fixture
def orchestration_connection(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = connect(tmp_path / "orchestration.sqlite")
    apply_migrations(connection)
    yield connection
    connection.close()


def _profile(
    repository: ModelControlRepository,
    role: ModelRole,
    name: str,
    *,
    setup_idempotency_key: str | None = None,
) -> str:
    profile = repository.create_profile(
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
            setup_idempotency_key=setup_idempotency_key,
        )
    )
    return profile.model_profile_uuid


def _certify(repository: ModelControlRepository, profile_uuid: str) -> None:
    profile = repository.get_profile(profile_uuid)
    assert profile is not None
    repository.record_health_event(
        profile_uuid,
        ProviderHealth(
            status="ok",
            provider_type=profile.provider_type,
            model_name=profile.model_name,
            latency_ms=1,
            local_only_safe=profile.endpoint_is_local,
            dns_or_host_reachable="reachable",
            tcp_or_http_reachable="reachable",
            authentication_status="valid",
            model_status="available",
            chat_completion_status="supported",
            structured_output_status="supported",
            role_compatibility_status="compatible",
            overall_status="ok",
            role_manifest_hash=role_contract_manifest_hash(profile.role),
            role_probe_status="compatible",
        ),
    )


def test_role_resolution_uses_project_workspace_global_precedence(
    orchestration_connection: sqlite3.Connection,
) -> None:
    repository = ModelControlRepository(orchestration_connection)
    workspace = WorkspaceRepository(orchestration_connection).create_workspace("Workspace")
    project = ProjectRepository(orchestration_connection).create_project(
        workspace.workspace_uuid,
        "Project",
    )
    global_uuid = _profile(repository, ModelRole.MEMORY_EXTRACTION, "global")
    workspace_uuid = _profile(repository, ModelRole.MEMORY_EXTRACTION, "workspace")
    project_uuid = _profile(repository, ModelRole.MEMORY_EXTRACTION, "project")
    repository.set_default(ModelRole.MEMORY_EXTRACTION, global_uuid)
    repository.set_default(
        ModelRole.MEMORY_EXTRACTION,
        workspace_uuid,
        workspace_uuid=workspace.workspace_uuid,
    )
    repository.set_default(
        ModelRole.MEMORY_EXTRACTION,
        project_uuid,
        workspace_uuid=workspace.workspace_uuid,
        project_uuid=project.project_uuid,
    )
    resolver = RoleResolutionService(repository)

    project_result = resolver.resolve(
        ModelRole.MEMORY_EXTRACTION,
        workspace_uuid=workspace.workspace_uuid,
        project_uuid=project.project_uuid,
    )
    workspace_result = resolver.resolve(
        ModelRole.MEMORY_EXTRACTION,
        workspace_uuid=workspace.workspace_uuid,
        project_uuid="project-other",
    )
    global_result = resolver.resolve(
        ModelRole.MEMORY_EXTRACTION,
        workspace_uuid="workspace-other",
    )

    assert (project_result.model_profile_uuid, project_result.scope_source) == (
        project_uuid,
        "project",
    )
    assert (workspace_result.model_profile_uuid, workspace_result.scope_source) == (
        workspace_uuid,
        "workspace",
    )
    assert (global_result.model_profile_uuid, global_result.scope_source) == (
        global_uuid,
        "global",
    )


def test_role_resolution_exposes_inheritance_and_unusable_fallback_reason(
    orchestration_connection: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = ModelControlRepository(orchestration_connection)
    extraction_uuid = _profile(repository, ModelRole.MEMORY_EXTRACTION, "extractor")
    repository.set_default(ModelRole.MEMORY_EXTRACTION, extraction_uuid)
    inherited = RoleResolutionService(repository).resolve(ModelRole.HIGH_CONFIDENCE_EXTRACTION)

    assert inherited.requested_role is ModelRole.HIGH_CONFIDENCE_EXTRACTION
    assert inherited.effective_role is ModelRole.MEMORY_EXTRACTION
    assert inherited.inheritance_source == "memory_extraction"
    assert inherited.model_profile_uuid == extraction_uuid

    remote = repository.create_profile(
        ModelProfileCreate(
            provider_type=ProviderType.OPENAI_COMPATIBLE_LLM,
            model_name="remote",
            role=ModelRole.PRIVACY_SENSITIVITY,
            endpoint_url="https://provider.example/v1",
            endpoint_is_local=False,
            supports_json_mode=True,
            secret_strategy="env_var",
            secret_env_var_name="MEMORIST_MISSING_TEST_SECRET",
            privacy_acknowledged=True,
        )
    )
    monkeypatch.setenv("MEMORIST_MISSING_TEST_SECRET", "test-only")
    _certify(repository, remote.model_profile_uuid)
    repository.set_default(ModelRole.PRIVACY_SENSITIVITY, remote.model_profile_uuid)
    monkeypatch.delenv("MEMORIST_MISSING_TEST_SECRET", raising=False)
    fallback = RoleResolutionService(repository).resolve(ModelRole.PRIVACY_SENSITIVITY)

    assert fallback.model_profile_uuid == extraction_uuid
    assert fallback.inheritance_source == "memory_extraction"

    repository.patch_profile(extraction_uuid, ModelProfilePatch(is_enabled=False))
    disabled_fallback = RoleResolutionService(repository).resolve(ModelRole.PRIVACY_SENSITIVITY)
    assert disabled_fallback.model_profile_uuid is None
    assert disabled_fallback.scope_source == "built_in_fallback"
    assert disabled_fallback.fallback_reason == "secret_reference_unavailable"
    disabled_inheritance = RoleResolutionService(repository).resolve(
        ModelRole.HIGH_CONFIDENCE_EXTRACTION
    )
    assert disabled_inheritance.fallback_reason == "configured_profile_disabled"


@pytest.mark.parametrize(
    ("endpoint", "expected", "operation_url"),
    [
        (
            "https://provider.example",
            "https://provider.example/v1",
            "https://provider.example/v1/chat/completions",
        ),
        (
            "https://provider.example/v1",
            "https://provider.example/v1",
            "https://provider.example/v1/chat/completions",
        ),
        (
            "https://provider.example/v1/chat/completions",
            "https://provider.example/v1",
            "https://provider.example/v1/chat/completions",
        ),
        (
            "https://provider.example/chat/completions",
            "https://provider.example/v1",
            "https://provider.example/v1/chat/completions",
        ),
        (
            "https://provider.example/custom/openai/",
            "https://provider.example/custom/openai",
            "https://provider.example/custom/openai/chat/completions",
        ),
        (
            "https://provider.example/custom/openai/chat/completions/",
            "https://provider.example/custom/openai",
            "https://provider.example/custom/openai/chat/completions",
        ),
    ],
)
def test_endpoint_normalization_never_duplicates_operation_paths(
    endpoint: str,
    expected: str,
    operation_url: str,
) -> None:
    normalized = normalize_openai_endpoint(endpoint)
    assert normalized.base_url == expected
    assert normalized.operation_url("chat/completions") == operation_url
    assert "/chat/completions/v1/chat/completions" not in operation_url


@pytest.mark.parametrize(
    "endpoint",
    [
        "provider.example/v1",
        "https://user:secret@provider.example/v1",
        "https://provider.example/v1?api_key=secret",
        "https://provider.example/v1#fragment",
        "https://provider.example/v1/chat/completions/v1/chat/completions",
        "https://provider.example/v1/chat/completions/proxy",
    ],
)
def test_endpoint_normalization_rejects_unsafe_values(endpoint: str) -> None:
    with pytest.raises(EndpointConfigurationError):
        normalize_openai_endpoint(endpoint)


def test_setup_profile_and_stage_invocation_are_idempotent_and_audited(
    orchestration_connection: sqlite3.Connection,
) -> None:
    repository = ModelControlRepository(orchestration_connection)
    first_uuid = _profile(
        repository,
        ModelRole.PRIVACY_SENSITIVITY,
        "privacy-v1",
        setup_idempotency_key="setup:privacy:test",
    )
    second_uuid = _profile(
        repository,
        ModelRole.PRIVACY_SENSITIVITY,
        "privacy-v2",
        setup_idempotency_key="setup:privacy:test",
    )
    repository.set_default(ModelRole.PRIVACY_SENSITIVITY, second_uuid)
    request = StageInvocationRequest(
        role=ModelRole.PRIVACY_SENSITIVITY,
        stage="privacy_sensitivity",
        source_type="memory_candidate",
        source_uuid="candidate-1",
        prompt_id="memorist.privacy_sensitivity",
        prompt_version="2.0",
        idempotency_key="privacy:candidate-1:test",
        input_payload={
            "candidate_text": "ordinary project context",
            "evidence_text": "ordinary project context",
        },
    )
    invoker = StageInvoker(orchestration_connection, repository)

    first = invoker.invoke_structured(
        request,
        validator=validate_privacy_result,
        deterministic_output=deterministic_privacy,
    )
    replay = invoker.invoke_structured(
        request,
        validator=validate_privacy_result,
        deterministic_output=deterministic_privacy,
    )

    assert first_uuid == second_uuid
    assert repository.get_profile(first_uuid).model_name == "privacy-v2"  # type: ignore[union-attr]
    assert replay.execution_uuid == first.execution_uuid
    assert replay.idempotent_replay is True
    # A replay must return the originally validated output, not None; otherwise
    # deterministic retry downgrades candidate lifecycle decisions to "abstain".
    assert first.output is not None
    assert replay.output == first.output
    assert (
        orchestration_connection.execute("SELECT COUNT(*) FROM processing_stage_runs").fetchone()[0]
        == 1
    )
    assert (
        orchestration_connection.execute(
            "SELECT COUNT(*) FROM prompt_execution_runs "
            "WHERE prompt_id = 'memorist.privacy_sensitivity'"
        ).fetchone()[0]
        == 1
    )
    assert (
        orchestration_connection.execute(
            "SELECT COUNT(*) FROM model_usage_events WHERE stage = 'privacy_sensitivity'"
        ).fetchone()[0]
        == 1
    )
    stored = orchestration_connection.execute(
        "SELECT output_ijson, output_hash FROM processing_stage_runs"
    ).fetchone()
    assert stored["output_ijson"] == dump_ijson(first.output)
    assert stored["output_hash"] == canonical_hash_ijson(first.output)


@pytest.mark.parametrize(
    ("stored_output", "stored_hash"),
    [
        (None, None),
        ("not-json", "not-a-hash"),
        (dump_ijson({"classification": "normal", "reason_codes": []}), "not-a-hash"),
    ],
)
def test_invalid_or_legacy_stage_output_is_reexecuted_and_repaired(
    orchestration_connection: sqlite3.Connection,
    stored_output: str | None,
    stored_hash: str | None,
) -> None:
    repository = ModelControlRepository(orchestration_connection)
    profile_uuid = _profile(
        repository,
        ModelRole.PRIVACY_SENSITIVITY,
        "privacy-repair",
    )
    repository.set_default(ModelRole.PRIVACY_SENSITIVITY, profile_uuid)
    request = StageInvocationRequest(
        role=ModelRole.PRIVACY_SENSITIVITY,
        stage="privacy_sensitivity",
        source_type="memory_candidate",
        source_uuid="candidate-repair",
        idempotency_key="privacy:candidate-repair",
        input_payload={
            "candidate_text": "ordinary project context",
            "evidence_text": "ordinary project context",
        },
    )
    invoker = StageInvoker(orchestration_connection, repository)
    initial = invoker.invoke_structured(
        request,
        validator=validate_privacy_result,
        deterministic_output=deterministic_privacy,
    )
    orchestration_connection.execute(
        """
        UPDATE processing_stage_runs
        SET output_ijson = ?, output_hash = ?
        WHERE stage_execution_uuid = ?
        """,
        (stored_output, stored_hash, initial.execution_uuid),
    )
    orchestration_connection.commit()
    calls = 0

    def counted_output(payload: dict[str, object]) -> dict[str, object]:
        nonlocal calls
        calls += 1
        return deterministic_privacy(payload)

    repaired = invoker.invoke_structured(
        request,
        validator=validate_privacy_result,
        deterministic_output=counted_output,
    )
    row = orchestration_connection.execute(
        "SELECT output_ijson, output_hash FROM processing_stage_runs "
        "WHERE stage_execution_uuid = ?",
        (initial.execution_uuid,),
    ).fetchone()

    assert calls == 1
    assert repaired.output == initial.output
    assert repaired.status == "ok"
    assert load_ijson(row["output_ijson"]) == repaired.output
    assert row["output_hash"] == canonical_hash_ijson(repaired.output)


def test_stage_replay_survives_database_restart(tmp_path: Path) -> None:
    db_path = tmp_path / "restart.sqlite"
    connection = connect(db_path)
    apply_migrations(connection)
    repository = ModelControlRepository(connection)
    profile_uuid = _profile(repository, ModelRole.PRIVACY_SENSITIVITY, "privacy-restart")
    repository.set_default(ModelRole.PRIVACY_SENSITIVITY, profile_uuid)
    request = StageInvocationRequest(
        role=ModelRole.PRIVACY_SENSITIVITY,
        stage="privacy_sensitivity",
        source_type="memory_candidate",
        source_uuid="candidate-restart",
        input_payload={
            "candidate_text": "ordinary project context",
            "evidence_text": "ordinary project context",
        },
    )
    first = StageInvoker(connection, repository).invoke_structured(
        request,
        validator=validate_privacy_result,
        deterministic_output=deterministic_privacy,
    )
    connection.close()

    restarted = connect(db_path)
    replay = StageInvoker(
        restarted,
        ModelControlRepository(restarted),
    ).invoke_structured(
        request,
        validator=validate_privacy_result,
        deterministic_output=deterministic_privacy,
    )

    assert replay.idempotent_replay is True
    assert replay.execution_uuid == first.execution_uuid
    assert replay.output == first.output
    assert restarted.execute("SELECT COUNT(*) FROM processing_stage_runs").fetchone()[0] == 1
    restarted.close()


def test_concurrent_stage_writers_return_one_authoritative_output(tmp_path: Path) -> None:
    db_path = tmp_path / "concurrent-stage.sqlite"
    setup = connect(db_path)
    apply_migrations(setup)
    repository = ModelControlRepository(setup)
    profile_uuid = _profile(repository, ModelRole.PRIVACY_SENSITIVITY, "privacy-race")
    repository.set_default(ModelRole.PRIVACY_SENSITIVITY, profile_uuid)
    setup.close()
    barrier = threading.Barrier(2)
    request = StageInvocationRequest(
        role=ModelRole.PRIVACY_SENSITIVITY,
        stage="privacy_sensitivity",
        source_type="memory_candidate",
        source_uuid="candidate-race",
        input_payload={
            "candidate_text": "ordinary project context",
            "evidence_text": "ordinary project context",
        },
    )

    def invoke() -> tuple[str, dict[str, object] | None]:
        connection = connect(db_path)
        try:

            def simultaneous(payload: dict[str, object]) -> dict[str, object]:
                barrier.wait(timeout=5)
                return deterministic_privacy(payload)

            result = StageInvoker(
                connection,
                ModelControlRepository(connection),
            ).invoke_structured(
                request,
                validator=validate_privacy_result,
                deterministic_output=simultaneous,
            )
            return result.execution_uuid, result.output
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: invoke(), range(2)))

    verify = connect(db_path)
    assert results[0] == results[1]
    assert verify.execute("SELECT COUNT(*) FROM processing_stage_runs").fetchone()[0] == 1
    verify.close()


def test_embedding_stage_disabled_resolution_records_abstained_result(
    orchestration_connection: sqlite3.Connection,
) -> None:
    repository = ModelControlRepository(orchestration_connection)
    invoker = StageInvoker(orchestration_connection, repository)
    result, vectors = invoker.invoke_embedding(
        StageInvocationRequest(
            role=ModelRole.EMBEDDING,
            stage="embedding_generation",
            source_type="memory_version",
            source_uuid="version-disabled",
            input_payload={"text": "hello"},
        ),
        texts=["hello"],
    )

    assert result.status == "abstained"
    assert result.called_provider is False
    assert vectors == []
    assert (
        orchestration_connection.execute(
            "SELECT COUNT(*) FROM processing_stage_runs WHERE source_uuid = 'version-disabled'"
        ).fetchone()[0]
        == 1
    )


def test_disabled_embedding_is_audited_and_outbox_is_terminally_skipped(
    tmp_path: Path,
) -> None:
    from memcore.config import Settings
    from memcore.memory_worker.pipeline import MemoryWorkerPipeline
    from memcore.repositories import MessageRepository, SessionRepository

    db_path = tmp_path / "embedding-disabled.sqlite"
    connection = connect(db_path)
    apply_migrations(connection)
    settings = Settings(
        db_path=str(db_path),
        object_store_path=str(tmp_path / "objects"),
    )
    session = SessionRepository(connection).create_session()
    message = MessageRepository(connection).create_message(
        session.session_uuid,
        role="user",
        creator_type="user",
        raw_text="Do not disable backups.",
    )

    MemoryWorkerPipeline(connection, settings).process_message(message.message_uuid)

    stage = connection.execute(
        """
        SELECT status, called_provider, embedding_count
        FROM processing_stage_runs
        WHERE stage = 'embedding_generation'
        """
    ).fetchone()
    outbox = connection.execute("SELECT status, attempts FROM embedding_outbox").fetchone()
    assert dict(stage) == {
        "status": "abstained",
        "called_provider": 0,
        "embedding_count": 0,
    }
    assert dict(outbox) == {"status": "skipped", "attempts": 1}
    assert connection.execute("SELECT COUNT(*) FROM memory_version_embeddings").fetchone()[0] == 0
    connection.close()


def test_embedding_stage_replay_bypass_regenerates_vectors(
    orchestration_connection: sqlite3.Connection,
) -> None:
    repository = ModelControlRepository(orchestration_connection)
    embedding_uuid = _profile(repository, ModelRole.EMBEDDING, "embedder")
    repository.set_default(ModelRole.EMBEDDING, embedding_uuid)
    invoker = StageInvoker(orchestration_connection, repository)
    request = StageInvocationRequest(
        role=ModelRole.EMBEDDING,
        stage="embedding_generation",
        source_type="memory_version",
        source_uuid="version-1",
        input_payload={"text": "embedding source text"},
    )

    first, first_vectors = invoker.invoke_embedding(request, texts=["embedding source text"])
    replay, replay_vectors = invoker.invoke_embedding(request, texts=["embedding source text"])
    regenerated, regenerated_vectors = invoker.invoke_embedding(
        request,
        texts=["embedding source text"],
        allow_replay=False,
    )

    assert first.status == "ok" and first_vectors
    assert replay.idempotent_replay is True and replay_vectors == []
    # When the caller's projection row is missing it must be able to bypass the
    # replay short-circuit and obtain real vectors again.
    assert regenerated.idempotent_replay is False
    assert regenerated_vectors == first_vectors
    assert (
        orchestration_connection.execute(
            "SELECT COUNT(*) FROM processing_stage_runs WHERE source_uuid = 'version-1'"
        ).fetchone()[0]
        == 1
    )


def test_embedding_projection_recovers_after_stage_persisted_crash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from memcore.config import Settings
    from memcore.memory_worker.pipeline import MemoryWorkerPipeline
    from memcore.repositories import MessageRepository, SessionRepository
    from memcore.repositories.retrieval import RetrievalRepository

    db_path = tmp_path / "embedding-recovery.sqlite"
    connection = connect(db_path)
    apply_migrations(connection)
    settings = Settings(
        db_path=str(db_path),
        object_store_path=str(tmp_path / "objects"),
    )
    repository = ModelControlRepository(connection)
    embedding_uuid = _profile(repository, ModelRole.EMBEDDING, "recovery-embedder")
    repository.set_default(ModelRole.EMBEDDING, embedding_uuid)
    session = SessionRepository(connection).create_session()
    message = MessageRepository(connection).create_message(
        session.session_uuid,
        role="user",
        creator_type="user",
        raw_text="Do not disable backups.",
    )
    original_upsert = RetrievalRepository.upsert_embedding
    crashed = False

    def crash_once(self: RetrievalRepository, embedding: object) -> object:
        nonlocal crashed
        if not crashed:
            crashed = True
            raise RuntimeError("injected crash after embedding stage persistence")
        return original_upsert(self, embedding)  # type: ignore[arg-type]

    monkeypatch.setattr(RetrievalRepository, "upsert_embedding", crash_once)
    with pytest.raises(RuntimeError, match="injected crash"):
        MemoryWorkerPipeline(connection, settings).process_message(message.message_uuid)

    memory_count = int(connection.execute("SELECT COUNT(*) FROM memories").fetchone()[0])
    assert (
        connection.execute(
            "SELECT COUNT(*) FROM processing_stage_runs WHERE stage = 'embedding_generation'"
        ).fetchone()[0]
        == 1
    )
    assert connection.execute("SELECT COUNT(*) FROM memory_version_embeddings").fetchone()[0] == 0
    outbox = connection.execute("SELECT status, attempts FROM embedding_outbox").fetchone()
    assert dict(outbox) == {"status": "running", "attempts": 1}

    MemoryWorkerPipeline(connection, settings).process_message(message.message_uuid)

    assert int(connection.execute("SELECT COUNT(*) FROM memories").fetchone()[0]) == memory_count
    assert connection.execute("SELECT COUNT(*) FROM memory_version_embeddings").fetchone()[0] == 1
    recovered_outbox = connection.execute(
        "SELECT status, attempts FROM embedding_outbox"
    ).fetchone()
    assert dict(recovered_outbox) == {"status": "succeeded", "attempts": 2}
    connection.close()


def test_candidate_stage_results_survive_processing_retry(tmp_path: Path) -> None:
    from memcore.config import Settings
    from memcore.memory_worker.pipeline import MemoryWorkerPipeline
    from memcore.repositories import MessageRepository, SessionRepository

    db_path = tmp_path / "retry.sqlite"
    connection = connect(db_path)
    apply_migrations(connection)
    settings = Settings(
        db_path=str(db_path),
        object_store_path=str(tmp_path / "objects"),
    )
    workspace = WorkspaceRepository(connection).create_workspace("Retry workspace")
    project = ProjectRepository(connection).create_project(
        workspace.workspace_uuid,
        "Retry project",
    )
    session = SessionRepository(connection).create_session(
        workspace_uuid=workspace.workspace_uuid,
        project_uuid=project.project_uuid,
    )
    messages = MessageRepository(connection)
    messages.create_message(
        session.session_uuid,
        role="user",
        creator_type="user",
        raw_text="یک طرح فنی یازده‌مرحله‌ای برای این پروژه تهیه کن.",
    )
    plan = "\n".join(
        ["طرح فنی یازده‌مرحله‌ای پروژه:"]
        + [
            f"{index}. مرحله شماره {index} برای پروژه با جزئیات کافی و توضیح کامل."
            for index in range(1, 12)
        ]
    )
    source = messages.create_message(
        session.session_uuid,
        role="assistant",
        creator_type="model",
        raw_text=plan,
    )

    result = MemoryWorkerPipeline(connection, settings).process_message(source.message_uuid)
    run_uuid = str(result["processing_run_uuid"])
    first_statuses = [
        str(row["status"])
        for row in connection.execute(
            "SELECT status FROM memory_candidates WHERE processing_run_uuid = ? "
            "ORDER BY candidate_uuid",
            (run_uuid,),
        ).fetchall()
    ]
    first_memories = int(connection.execute("SELECT COUNT(*) FROM memories").fetchone()[0])
    assert "ready_for_consolidation" in first_statuses

    # Simulate a crash after candidate stages were persisted but before the run
    # was marked succeeded, then retry through the production pipeline path.
    connection.execute(
        "UPDATE memory_processing_runs SET status = 'failed' WHERE processing_run_uuid = ?",
        (run_uuid,),
    )
    connection.commit()
    MemoryWorkerPipeline(connection, settings).process_message(source.message_uuid)

    retry_statuses = [
        str(row["status"])
        for row in connection.execute(
            "SELECT status FROM memory_candidates WHERE processing_run_uuid = ? "
            "ORDER BY candidate_uuid",
            (run_uuid,),
        ).fetchall()
    ]
    retry_memories = int(connection.execute("SELECT COUNT(*) FROM memories").fetchone()[0])
    assert retry_statuses == first_statuses
    assert retry_memories == first_memories
    connection.close()
