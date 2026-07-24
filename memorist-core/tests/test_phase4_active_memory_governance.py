import sqlite3
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest

from memcore.active_memory.compaction.compactor import BlockCompactor
from memcore.active_memory.materializer import ActiveMemoryMaterializer
from memcore.active_memory.repositories import ActiveMemoryRepository
from memcore.active_memory.selectors import select_canonical_sources
from memcore.config import Settings
from memcore.governance.correction import MemoryCorrectionService
from memcore.governance.delivery import DeliveryTraceService
from memcore.governance.feedback import FeedbackService
from memcore.governance.inspection import MemoryInspectionService
from memcore.governance.privacy import PrivacyService
from memcore.governance.repositories import GovernanceRepository
from memcore.memory_worker.fencing import LeaseFenceRejected
from memcore.memory_worker.pipeline import MemoryWorkerPipeline
from memcore.model_control.providers.base import StructuredCompletionResponse
from memcore.model_control.repository import ModelControlRepository
from memcore.model_control.schemas import ModelProfileCreate, ProviderType
from memcore.models import MemoryBlockType, Message, ModelRole
from memcore.preflight import PreflightRequest, PreflightService
from memcore.repositories import (
    MemoryBlockRepository,
    MessageRepository,
    ProjectRepository,
    SessionRepository,
    WorkspaceRepository,
)
from memcore.repositories.domain import RepositoryError
from memcore.repositories.memory_worker import MemoryStoreRepository
from memcore.retrieval.runner import RetrievalRunner
from memcore.retrieval.semantic import SemanticGenerator
from memcore.storage.migrations import apply_migrations
from memcore.storage.sqlite import connect
from memcore.validators.ijson import load_ijson


@pytest.fixture()
def connection(tmp_path: Path) -> Generator[sqlite3.Connection, None, None]:
    sqlite_connection = connect(tmp_path / "phase4.sqlite")
    apply_migrations(sqlite_connection)
    try:
        yield sqlite_connection
    finally:
        sqlite_connection.close()


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    return Settings(
        db_path=str(tmp_path / "phase4.sqlite"),
        object_store_path=str(tmp_path / "objects"),
        preflight_timeout_ms=10_000,
    )


def test_phase4_migration_tables_and_block_columns_exist(
    connection: sqlite3.Connection,
) -> None:
    tables = {
        row["name"]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table', 'virtual table')"
        )
    }
    columns = {row["name"] for row in connection.execute("PRAGMA table_info(memory_blocks)")}

    assert {
        "memory_block_build_runs",
        "memory_block_versions",
        "memory_block_sources",
        "memory_delivery_events",
        "response_memory_attributions",
        "memory_feedback",
        "memory_change_requests",
        "privacy_requests",
        "privacy_request_items",
        "erasure_receipts",
    }.issubset(tables)
    assert {
        "current_version_uuid",
        "builder_policy_ijson",
        "optimistic_lock_version",
        "source_snapshot_hash",
        "build_status",
    }.issubset(columns)


def test_active_block_build_versions_sources_and_coverage(
    connection: sqlite3.Connection,
    settings: Settings,
) -> None:
    workspace = WorkspaceRepository(connection).create_workspace("Workspace")
    project = ProjectRepository(connection).create_project(workspace.workspace_uuid, "Project")
    session = SessionRepository(connection).create_session(
        workspace_uuid=workspace.workspace_uuid,
        project_uuid=project.project_uuid,
    )
    _process_message(connection, settings, session.session_uuid, "Do not use cloud storage.")
    block = MemoryBlockRepository(connection).create_block(
        MemoryBlockType.PROJECT_CONTEXT,
        scope_type="project",
        scope_uuid=project.project_uuid,
        label="Project context",
        value="empty",
        char_limit=400,
    )

    result = ActiveMemoryMaterializer(connection).build(block.block_uuid)
    versions = ActiveMemoryRepository(connection).list_versions(block.block_uuid)
    sources = ActiveMemoryRepository(connection).list_sources(block.block_uuid)
    coverage = ActiveMemoryMaterializer(connection).coverage(block.block_uuid)

    assert result.version_number == 1
    assert len(versions) == 1
    assert sources[0]["source_role"] == "constraint"
    assert coverage.constraint_coverage == 1.0
    assert "Do not use cloud storage" in result.value


def test_configured_compaction_provider_changes_block_and_replays_idempotently(
    connection: sqlite3.Connection,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = WorkspaceRepository(connection).create_workspace("Compaction workspace")
    project = ProjectRepository(connection).create_project(
        workspace.workspace_uuid,
        "Compaction project",
    )
    session = SessionRepository(connection).create_session(
        workspace_uuid=workspace.workspace_uuid,
        project_uuid=project.project_uuid,
    )
    _process_message(
        connection,
        settings,
        session.session_uuid,
        "Do not disable project backups.",
    )
    block = MemoryBlockRepository(connection).create_block(
        MemoryBlockType.PROJECT_CONTEXT,
        scope_type="project",
        scope_uuid=project.project_uuid,
        label="Project context",
        value="empty",
        char_limit=1200,
    )
    source = select_canonical_sources(connection, block)[0]
    _configure_compaction_profile(connection)
    calls = 0

    class Provider:
        def complete_structured(
            self,
            prompt: str,
            timeout_seconds: float = 1.0,
        ) -> StructuredCompletionResponse:
            del prompt, timeout_seconds
            nonlocal calls
            calls += 1
            return StructuredCompletionResponse(
                output_ijson={
                    "summary": source.normalized_text,
                    "items": [
                        {
                            "text": source.normalized_text,
                            "source_memory_uuids": [source.memory_uuid],
                            "source_memory_version_uuids": [
                                source.memory_version_uuid
                            ],
                        }
                    ],
                    "excluded_memory_version_uuids": [],
                    "conflicts": [],
                    "status": "ok",
                },
                input_tokens=20,
                output_tokens=10,
                latency_ms=1,
            )

    monkeypatch.setattr(
        "memcore.model_control.stage_invocation.provider_for_profile",
        lambda profile: Provider(),
    )

    first = BlockCompactor(connection).compact(block.block_uuid)
    replay = BlockCompactor(connection).compact(block.block_uuid)
    versions = ActiveMemoryRepository(connection).list_versions(block.block_uuid)
    structured = load_ijson(versions[0]["structured_value_ijson"])

    assert first["provider_output_applied"] is True
    assert first["published"] is True
    assert f"Summary: {source.normalized_text}" in str(first["value"])
    assert structured["content_source"] == "provider"
    assert structured["items"][0]["source_provenance"][0]["source_authority"]
    assert replay["block_version_uuid"] == first["block_version_uuid"]
    assert calls == 1
    assert len(versions) == 1


def test_invalid_provider_compaction_preserves_previous_valid_block(
    connection: sqlite3.Connection,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = WorkspaceRepository(connection).create_workspace("Fallback workspace")
    project = ProjectRepository(connection).create_project(
        workspace.workspace_uuid,
        "Fallback project",
    )
    session = SessionRepository(connection).create_session(
        workspace_uuid=workspace.workspace_uuid,
        project_uuid=project.project_uuid,
    )
    _process_message(connection, settings, session.session_uuid, "Do not disable backups.")
    block = MemoryBlockRepository(connection).create_block(
        MemoryBlockType.PROJECT_CONTEXT,
        scope_type="project",
        scope_uuid=project.project_uuid,
        label="Project context",
        value="empty",
        char_limit=1200,
    )
    previous = ActiveMemoryMaterializer(connection).build(block.block_uuid)
    _configure_compaction_profile(connection)

    class InvalidProvider:
        def complete_structured(
            self,
            prompt: str,
            timeout_seconds: float = 1.0,
        ) -> StructuredCompletionResponse:
            del prompt, timeout_seconds
            return StructuredCompletionResponse(
                output_ijson={
                    "summary": "invented unsupported claim",
                    "items": [
                        {
                            "text": "invented unsupported claim",
                            "source_memory_uuids": ["unsupported-memory"],
                            "source_memory_version_uuids": ["unsupported-version"],
                        }
                    ],
                    "excluded_memory_version_uuids": [],
                    "conflicts": [],
                    "status": "ok",
                },
                input_tokens=1,
                output_tokens=1,
                latency_ms=1,
            )

    monkeypatch.setattr(
        "memcore.model_control.stage_invocation.provider_for_profile",
        lambda profile: InvalidProvider(),
    )
    result = BlockCompactor(connection).compact(block.block_uuid)

    assert result["published"] is False
    assert result["provider_output_applied"] is False
    assert result["fallback"] == "previous_valid_block_preserved"
    assert result["block_version_uuid"] == previous.block_version_uuid
    assert len(ActiveMemoryRepository(connection).list_versions(block.block_uuid)) == 1
    stage = connection.execute(
        "SELECT status, detail_sanitized FROM processing_stage_runs "
        "WHERE stage = 'block_compaction'"
    ).fetchone()
    assert stage["status"] == "abstained"
    assert stage["detail_sanitized"] == "context validation rejected compaction proposal"


def test_compaction_source_change_during_provider_preserves_previous_block(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "compaction-source-change.sqlite"
    connection = connect(db_path)
    apply_migrations(connection)
    settings = Settings(
        db_path=str(db_path),
        object_store_path=str(tmp_path / "objects"),
    )
    workspace = WorkspaceRepository(connection).create_workspace("Changing workspace")
    project = ProjectRepository(connection).create_project(
        workspace.workspace_uuid,
        "Changing project",
    )
    session = SessionRepository(connection).create_session(
        workspace_uuid=workspace.workspace_uuid,
        project_uuid=project.project_uuid,
    )
    _process_message(connection, settings, session.session_uuid, "Do not disable backups.")
    block = MemoryBlockRepository(connection).create_block(
        MemoryBlockType.PROJECT_CONTEXT,
        scope_type="project",
        scope_uuid=project.project_uuid,
        label="Project context",
        value="empty",
        char_limit=1200,
    )
    previous = ActiveMemoryMaterializer(connection).build(block.block_uuid)
    source = select_canonical_sources(connection, block)[0]
    _configure_compaction_profile(connection)

    class MutatingProvider:
        def complete_structured(
            self,
            prompt: str,
            timeout_seconds: float = 1.0,
        ) -> StructuredCompletionResponse:
            del prompt, timeout_seconds
            concurrent = connect(db_path)
            try:
                other_session = SessionRepository(concurrent).create_session(
                    workspace_uuid=workspace.workspace_uuid,
                    project_uuid=project.project_uuid,
                )
                _process_message(
                    concurrent,
                    settings,
                    other_session.session_uuid,
                    "Do not disable audit logs.",
                )
            finally:
                concurrent.close()
            return StructuredCompletionResponse(
                output_ijson=_provider_compaction_output(source),
                input_tokens=1,
                output_tokens=1,
                latency_ms=1,
            )

    monkeypatch.setattr(
        "memcore.model_control.stage_invocation.provider_for_profile",
        lambda profile: MutatingProvider(),
    )
    result = BlockCompactor(connection).compact(block.block_uuid)

    assert result["published"] is False
    assert result["block_version_uuid"] == previous.block_version_uuid
    assert result["fallback_reason"] == "source snapshot changed during provider execution"
    assert len(ActiveMemoryRepository(connection).list_versions(block.block_uuid)) == 1
    connection.close()


def test_compaction_lease_loss_after_provider_fences_stage_and_block_writes(
    connection: sqlite3.Connection,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = SessionRepository(connection).create_session()
    _process_message(connection, settings, session.session_uuid, "Do not disable backups.")
    block = MemoryBlockRepository(connection).create_block(
        MemoryBlockType.PROJECT_CONTEXT,
        scope_type="session",
        scope_uuid=session.session_uuid,
        label="Session context",
        value="empty",
        char_limit=1200,
    )
    source = select_canonical_sources(connection, block)[0]
    _configure_compaction_profile(connection)
    lost = False

    class Provider:
        def complete_structured(
            self,
            prompt: str,
            timeout_seconds: float = 1.0,
        ) -> StructuredCompletionResponse:
            del prompt, timeout_seconds
            nonlocal lost
            lost = True
            return StructuredCompletionResponse(
                output_ijson=_provider_compaction_output(source),
                input_tokens=1,
                output_tokens=1,
                latency_ms=1,
            )

    def fence(**kwargs: object) -> None:
        del kwargs
        if lost:
            raise LeaseFenceRejected("test lease lost")

    monkeypatch.setattr(
        "memcore.model_control.stage_invocation.provider_for_profile",
        lambda profile: Provider(),
    )
    with pytest.raises(LeaseFenceRejected, match="test lease lost"):
        BlockCompactor(connection).compact(block.block_uuid, lease_fence=fence)

    assert connection.execute(
        "SELECT COUNT(*) FROM processing_stage_runs WHERE stage = 'block_compaction'"
    ).fetchone()[0] == 0
    assert len(ActiveMemoryRepository(connection).list_versions(block.block_uuid)) == 0


def test_read_only_safety_block_requires_trusted_actor(
    connection: sqlite3.Connection,
) -> None:
    block = MemoryBlockRepository(connection).create_block(
        MemoryBlockType.SAFETY_PRIVACY,
        scope_type="workspace",
        scope_uuid=None,
        label="Safety",
        value="empty",
        char_limit=400,
        read_only=True,
    )

    with pytest.raises(RepositoryError, match="not authorized"):
        ActiveMemoryMaterializer(connection).build(block.block_uuid, actor_type="user")


def test_block_correction_invalidates_dependencies_and_rollback(
    connection: sqlite3.Connection,
    settings: Settings,
) -> None:
    workspace = WorkspaceRepository(connection).create_workspace("Workspace")
    project = ProjectRepository(connection).create_project(workspace.workspace_uuid, "Project")
    session = SessionRepository(connection).create_session(
        workspace_uuid=workspace.workspace_uuid,
        project_uuid=project.project_uuid,
    )
    _process_message(connection, settings, session.session_uuid, "We decided to use SQLite.")
    memory = MemoryStoreRepository(connection).list_memories()[0]
    block = MemoryBlockRepository(connection).create_block(
        MemoryBlockType.PROJECT_CONTEXT,
        scope_type="project",
        scope_uuid=project.project_uuid,
        label="Project context",
        value="empty",
        char_limit=400,
    )
    first = ActiveMemoryMaterializer(connection).build(block.block_uuid)
    request = MemoryCorrectionService(connection).create_request(
        memory.memory_uuid,
        "correct",
        actor_type="user",
        proposed_value={
            "normalized_text": "decision:project:decision:We decided to use PostgreSQL"
        },
    )

    applied = MemoryCorrectionService(connection).apply_request(request["change_request_uuid"])
    stale_block = MemoryBlockRepository(connection).get_block(block.block_uuid)
    second = ActiveMemoryMaterializer(connection).build(block.block_uuid)
    rollback = ActiveMemoryRepository(connection).rollback(block.block_uuid, first.version_number)

    assert applied["status"] == "applied"
    assert stale_block is not None
    assert stale_block.build_status == "stale"
    assert "PostgreSQL" in second.value
    assert rollback["block_version_uuid"] == first.block_version_uuid


def test_delivery_trace_feedback_and_unused_attribution(
    connection: sqlite3.Connection,
    settings: Settings,
) -> None:
    session = SessionRepository(connection).create_session()
    source = _process_message(
        connection, settings, session.session_uuid, "I prefer concise answers."
    )
    query = MessageRepository(connection).create_message(
        session.session_uuid,
        role="user",
        creator_type="user",
        raw_text="What is my answer preference?",
    )
    response = PreflightService(connection, settings).run(
        PreflightRequest(
            session_uuid=session.session_uuid,
            input_message_uuid=query.message_uuid,
            retrieval_mode="standard",
            token_budget=800,
        )
    )
    assert response.attachment_uuid is not None
    repository = GovernanceRepository(connection)
    assistant = MessageRepository(connection).create_message(
        session.session_uuid,
        role="assistant",
        creator_type="memory_augmented_model",
        raw_text="I cannot tell from context.",
    )
    DeliveryTraceService(connection).attribute_response(
        assistant.message_uuid,
        response.attachment_uuid,
        assistant.raw_text or "",
    )
    trace = DeliveryTraceService(connection).response_trace(assistant.message_uuid)
    feedback = FeedbackService(connection).submit_feedback(
        "outdated",
        actor_type="user",
        memory_uuid=MemoryStoreRepository(connection).list_memories()[0].memory_uuid,
        comment="This is old.",
    )

    with pytest.raises(RepositoryError, match="offsets"):
        repository.record_attribution(
            assistant.message_uuid,
            MemoryStoreRepository(connection).list_memories()[0].memory_uuid,
            source.message_uuid,
            "manual",
            "supported",
            "user_feedback",
            response_span_text="bad span",
        )
    assert any(item["attribution_status"] == "unused" for item in trace["attributions"])
    assert feedback["change_request_uuid"]


def test_confirm_and_undo_create_versions_without_raising_confidence(
    connection: sqlite3.Connection,
    settings: Settings,
) -> None:
    session = SessionRepository(connection).create_session()
    _process_message(connection, settings, session.session_uuid, "I prefer concise answers.")
    store = MemoryStoreRepository(connection)
    memory = store.list_memories()[0]
    initial_confidence = store.list_versions(memory.memory_uuid)[0].confidence
    confirm = MemoryCorrectionService(connection).create_request(
        memory.memory_uuid,
        "confirm",
        actor_type="user",
    )
    MemoryCorrectionService(connection).apply_request(confirm["change_request_uuid"])
    undo = MemoryCorrectionService(connection).undo_request(confirm["change_request_uuid"])
    versions = store.list_versions(memory.memory_uuid)
    inspected = MemoryInspectionService(connection).inspect_memory(memory.memory_uuid)

    assert undo["status"] == "applied"
    assert len(versions) == 3
    assert versions[-1].confidence == initial_confidence
    assert inspected["evidence"]


def test_privacy_forget_memory_quarantines_and_verifies_dependencies(
    connection: sqlite3.Connection,
    settings: Settings,
) -> None:
    session = SessionRepository(connection).create_session()
    _process_message(connection, settings, session.session_uuid, "I prefer concise answers.")
    memory = MemoryStoreRepository(connection).list_memories()[0]
    plan = RetrievalRunner(connection, settings).plan(
        session.session_uuid,
        MessageRepository(connection)
        .create_message(
            session.session_uuid,
            role="user",
            creator_type="user",
            raw_text="How verbose should replies be?",
        )
        .message_uuid,
        "standard",
        800,
    )[1]
    SemanticGenerator(connection).generate(plan)
    preview = PrivacyService(connection).preview_request(
        "forget_memory",
        "memory",
        {"memory_uuid": memory.memory_uuid},
        actor_type="user",
        target_uuid=memory.memory_uuid,
    )
    PrivacyService(connection).confirm_request(
        preview["privacy_request_uuid"],
        preview["confirmation_token"],
    )
    receipt = PrivacyService(connection).execute_request(preview["privacy_request_uuid"])
    retry_receipt = PrivacyService(connection).retry_request(preview["privacy_request_uuid"])

    forgotten = MemoryStoreRepository(connection).get_memory(memory.memory_uuid)
    fts_count = connection.execute(
        "SELECT COUNT(*) FROM memory_version_fts WHERE memory_uuid = ?",
        (memory.memory_uuid,),
    ).fetchone()[0]
    embedding_count = connection.execute(
        "SELECT COUNT(*) FROM memory_version_embeddings"
    ).fetchone()[0]

    assert forgotten is not None
    assert forgotten.status.value == "forgotten"
    assert fts_count == 0
    assert embedding_count == 0
    assert receipt["erasure_receipt_uuid"] == retry_receipt["erasure_receipt_uuid"]
    assert "concise" not in str(receipt)


def test_delete_message_redacts_derived_units(
    connection: sqlite3.Connection,
    settings: Settings,
) -> None:
    session = SessionRepository(connection).create_session()
    message = _process_message(
        connection, settings, session.session_uuid, "I prefer concise answers."
    )
    preview = PrivacyService(connection).preview_request(
        "delete_message",
        "message",
        {"message_uuid": message.message_uuid},
        actor_type="user",
        target_uuid=message.message_uuid,
    )
    PrivacyService(connection).confirm_request(
        preview["privacy_request_uuid"],
        preview["confirmation_token"],
    )
    PrivacyService(connection).execute_request(preview["privacy_request_uuid"])
    redacted = MessageRepository(connection).get_message(message.message_uuid)
    unit_texts = [row["text"] for row in connection.execute("SELECT text FROM text_units")]

    assert redacted is not None
    assert redacted.raw_text is None
    assert redacted.redaction_status == "erased"
    assert unit_texts == ["[erased]"]


def _process_message(
    connection: sqlite3.Connection,
    settings: Settings,
    session_uuid: str,
    raw_text: str,
) -> Message:
    message = MessageRepository(connection).create_message(
        session_uuid,
        role="user",
        creator_type="user",
        raw_text=raw_text,
    )
    MemoryWorkerPipeline(connection, settings).process_message(message.message_uuid)
    return message


def _configure_compaction_profile(connection: sqlite3.Connection) -> str:
    repository = ModelControlRepository(connection)
    profile = repository.create_profile(
        ModelProfileCreate(
            profile_name="Test block compactor",
            provider_type=ProviderType.OPENAI_COMPATIBLE_LLM,
            provider_name="test provider",
            model_name="test-compactor",
            role=ModelRole.BLOCK_COMPACTION,
            endpoint_url="http://127.0.0.1:9/v1",
            endpoint_is_local=True,
            supports_structured_output=True,
            supports_json_mode=True,
            privacy_acknowledged=True,
        )
    )
    repository.set_default(ModelRole.BLOCK_COMPACTION, profile.model_profile_uuid)
    return profile.model_profile_uuid


def _provider_compaction_output(source: Any) -> dict[str, Any]:
    return {
        "summary": source.normalized_text,
        "items": [
            {
                "text": source.normalized_text,
                "source_memory_uuids": [source.memory_uuid],
                "source_memory_version_uuids": [source.memory_version_uuid],
            }
        ],
        "excluded_memory_version_uuids": [],
        "conflicts": [],
        "status": "ok",
    }
