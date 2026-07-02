import sqlite3
from collections.abc import Generator
from pathlib import Path

import pytest

from memcore.active_memory.materializer import ActiveMemoryMaterializer
from memcore.active_memory.repositories import ActiveMemoryRepository
from memcore.config import Settings
from memcore.governance.correction import MemoryCorrectionService
from memcore.governance.delivery import DeliveryTraceService
from memcore.governance.feedback import FeedbackService
from memcore.governance.inspection import MemoryInspectionService
from memcore.governance.privacy import PrivacyService
from memcore.governance.repositories import GovernanceRepository
from memcore.memory_worker.pipeline import MemoryWorkerPipeline
from memcore.models import MemoryBlockType, Message
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
