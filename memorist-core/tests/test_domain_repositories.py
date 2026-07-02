import sqlite3
from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from memcore.models import (
    AttachmentMode,
    JobStatus,
    MemoryBlockType,
    MessageRole,
    ModelRole,
    ProcessingStatus,
    SessionStatus,
)
from memcore.repositories import (
    EventRepository,
    JobRepository,
    MemoryBlockRepository,
    MemoryContextAttachmentRepository,
    MessageRepository,
    MessageVersionRepository,
    ModelProfileRepository,
    ProjectRepository,
    RepositoryError,
    SessionHotCacheRepository,
    SessionRepository,
    WorkspaceRepository,
)
from memcore.storage.migrations import apply_migrations
from memcore.storage.sqlite import connect
from memcore.validators.ijson import IJsonValidationError


@pytest.fixture()
def connection(tmp_path: Path) -> Generator[sqlite3.Connection, None, None]:
    sqlite_connection = connect(tmp_path / "memorist.sqlite")
    apply_migrations(sqlite_connection)
    try:
        yield sqlite_connection
    finally:
        sqlite_connection.close()


def test_create_get_list_workspace(connection: sqlite3.Connection) -> None:
    repository = WorkspaceRepository(connection)

    workspace = repository.create_workspace("Local workspace", "Private memory")

    assert_is_uuid(workspace.workspace_uuid)
    assert_is_utc_timestamp(workspace.created_at)
    assert repository.get_workspace(workspace.workspace_uuid) == workspace
    assert repository.list_workspaces() == [workspace]


def test_create_get_list_project(connection: sqlite3.Connection) -> None:
    workspace = WorkspaceRepository(connection).create_workspace("Workspace")
    repository = ProjectRepository(connection)

    project = repository.create_project(workspace.workspace_uuid, "Project", "Phase 1")

    assert_is_uuid(project.project_uuid)
    assert_is_utc_timestamp(project.created_at)
    assert repository.get_project(project.project_uuid) == project
    assert repository.list_projects(workspace.workspace_uuid) == [project]
    assert repository.list_projects() == [project]


def test_create_session(connection: sqlite3.Connection) -> None:
    workspace = WorkspaceRepository(connection).create_workspace("Workspace")
    project = ProjectRepository(connection).create_project(workspace.workspace_uuid, "Project")
    repository = SessionRepository(connection)

    session = repository.create_session(
        workspace_uuid=workspace.workspace_uuid,
        project_uuid=project.project_uuid,
        openwebui_conversation_id="conv-1",
        title="Session",
    )
    updated = repository.update_session_state(
        session.session_uuid,
        conceptual_state_text="Working on repository contracts",
        status=SessionStatus.ARCHIVED,
    )

    assert_is_uuid(session.session_uuid)
    assert_is_utc_timestamp(session.created_at)
    assert updated.status is SessionStatus.ARCHIVED
    assert updated.conceptual_state_text == "Working on repository contracts"
    assert repository.get_session(session.session_uuid) == updated


def test_append_events_with_monotonic_event_index(connection: sqlite3.Connection) -> None:
    session = SessionRepository(connection).create_session()
    repository = EventRepository(connection)

    first = repository.append_event(session.session_uuid, "session_started", {"sequence": 1})
    second = repository.append_event(session.session_uuid, "session_updated", {"sequence": 2})

    assert first.event_index == 0
    assert second.event_index == 1
    assert first.content_hash is not None
    assert repository.list_events(session.session_uuid) == [first, second]


def test_create_message_and_verify_event_emitted(connection: sqlite3.Connection) -> None:
    session = SessionRepository(connection).create_session()
    messages = MessageRepository(connection)

    message = messages.create_message(
        session.session_uuid,
        role=MessageRole.USER,
        creator_type="user",
        raw_text="Remember this",
        raw_payload={"source": "open_webui"},
        snapshot={"visible": True},
    )
    events = EventRepository(connection).list_events(session.session_uuid)

    assert_is_uuid(message.message_uuid)
    assert message.turn_index == 0
    assert message.processing_status is ProcessingStatus.PENDING
    assert events[-1].event_type == "message_created"
    assert messages.list_messages(session.session_uuid) == [message]


def test_create_message_version_and_verify_event_emitted(connection: sqlite3.Connection) -> None:
    session = SessionRepository(connection).create_session()
    message = MessageRepository(connection).create_message(
        session.session_uuid,
        role="assistant",
        creator_type="model",
        raw_text="Initial",
    )
    versions = MessageVersionRepository(connection)

    version = versions.create_version(
        message.message_uuid,
        raw_text="Edited",
        raw_payload={"revision": 1},
        change_reason="manual correction",
        created_by="tester",
    )
    events = EventRepository(connection).list_events(session.session_uuid)

    assert_is_uuid(version.message_version_uuid)
    assert version.version_number == 1
    assert versions.list_versions(message.message_uuid) == [version]
    assert events[-1].event_type == "message_version_created"


def test_soft_delete_message_and_verify_event_emitted(connection: sqlite3.Connection) -> None:
    session = SessionRepository(connection).create_session()
    repository = MessageRepository(connection)
    message = repository.create_message(session.session_uuid, role="user", creator_type="user")

    deleted = repository.soft_delete_message(message.message_uuid)
    events = EventRepository(connection).list_events(session.session_uuid)

    assert deleted.is_deleted is True
    assert deleted.visibility == "deleted"
    assert events[-1].event_type == "message_deleted"


def test_create_model_profile_without_secrets(connection: sqlite3.Connection) -> None:
    repository = ModelProfileRepository(connection)

    profile = repository.create_model_profile(
        provider="local",
        model_name="llama-local",
        role=ModelRole.MAIN_CHAT,
        is_local=True,
        supports_json_mode=True,
        pricing={"input_per_1k": "0"},
        metadata={"context_window": 8192},
    )

    assert_is_uuid(profile.model_profile_uuid)
    assert profile.role is ModelRole.MAIN_CHAT
    assert repository.get_model_profile(profile.model_profile_uuid) == profile
    assert repository.list_model_profiles(ModelRole.MAIN_CHAT) == [profile]

    with pytest.raises(RepositoryError, match="secret key"):
        repository.create_model_profile(
            provider="bad",
            model_name="remote",
            role="main_chat",
            metadata={"api_key": "do-not-store"},
        )


def test_enqueue_and_claim_job(connection: sqlite3.Connection) -> None:
    repository = JobRepository(connection)
    low = repository.enqueue_job("low_priority", {"task": "later"}, priority=10)
    high = repository.enqueue_job("high_priority", {"task": "now"}, priority=90)

    claimed = repository.claim_next_job()

    assert low.status is JobStatus.PENDING
    assert claimed is not None
    assert claimed.job_uuid == high.job_uuid
    assert claimed.status is JobStatus.RUNNING
    assert claimed.attempts == 1
    assert repository.mark_job_succeeded(claimed.job_uuid).status is JobStatus.SUCCEEDED


def test_invalid_ijson_payload_rejected(connection: sqlite3.Connection) -> None:
    session = SessionRepository(connection).create_session()

    with pytest.raises(IJsonValidationError, match="non-finite"):
        EventRepository(connection).append_event(
            session.session_uuid,
            "invalid_payload",
            {"value": float("nan")},
        )


def test_create_memory_context_attachment_placeholder(connection: sqlite3.Connection) -> None:
    workspace = WorkspaceRepository(connection).create_workspace("Workspace")
    project = ProjectRepository(connection).create_project(workspace.workspace_uuid, "Project")
    session = SessionRepository(connection).create_session(
        workspace_uuid=workspace.workspace_uuid,
        project_uuid=project.project_uuid,
    )
    repository = MemoryContextAttachmentRepository(connection)

    attachment = repository.create_attachment(
        session.session_uuid,
        AttachmentMode.LITE,
        {"blocks": [], "created_at": "2026-06-25T20:00:00Z"},
        project_uuid=project.project_uuid,
        rendered_attachment="",
        source_block_uuids=[],
        source_memory_uuids=[],
        token_count=0,
    )

    assert_is_uuid(attachment.attachment_uuid)
    assert attachment.attachment_mode is AttachmentMode.LITE
    assert repository.get_attachment(attachment.attachment_uuid) == attachment
    assert repository.list_attachments(session.session_uuid) == [attachment]


def test_create_memory_block_placeholder(connection: sqlite3.Connection) -> None:
    workspace = WorkspaceRepository(connection).create_workspace("Workspace")
    repository = MemoryBlockRepository(connection)

    block = repository.create_block(
        MemoryBlockType.USER_PROFILE,
        scope_type="workspace",
        scope_uuid=workspace.workspace_uuid,
        label="User profile",
        value="Prefers concise answers",
        char_limit=500,
        source_memory_uuids=[],
    )

    assert_is_uuid(block.block_uuid)
    assert block.block_type is MemoryBlockType.USER_PROFILE
    assert repository.get_block(block.block_uuid) == block
    assert repository.list_blocks(
        scope_type="workspace",
        scope_uuid=workspace.workspace_uuid,
    ) == [block]


def test_upsert_session_hot_cache_placeholder(connection: sqlite3.Connection) -> None:
    session = SessionRepository(connection).create_session()
    repository = SessionHotCacheRepository(connection)

    first = repository.upsert_cache(
        session.session_uuid,
        current_problem="Build repositories",
        active_constraints={"local_only": True},
        unresolved_questions=[],
        recent_entities=["Memorist"],
        recent_memory_uuids=[],
    )
    second = repository.upsert_cache(
        session.session_uuid,
        current_problem="Validate repositories",
        current_plan="Run tests",
        recent_memory_uuids=[],
    )

    assert first.session_uuid == session.session_uuid
    assert second.current_problem == "Validate repositories"
    assert repository.get_cache(session.session_uuid) == second


def test_all_generated_ids_and_timestamps_are_utc_strings(
    connection: sqlite3.Connection,
) -> None:
    workspace = WorkspaceRepository(connection).create_workspace("Workspace")
    session = SessionRepository(connection).create_session(workspace_uuid=workspace.workspace_uuid)
    event = EventRepository(connection).append_event(session.session_uuid, "check", {"ok": True})
    job = JobRepository(connection).enqueue_job("check", {"ok": True})

    for uuid_value in (
        workspace.workspace_uuid,
        session.session_uuid,
        event.event_uuid,
        job.job_uuid,
    ):
        assert_is_uuid(uuid_value)

    for timestamp in (workspace.created_at, session.created_at, event.created_at, job.created_at):
        assert_is_utc_timestamp(timestamp)


def assert_is_uuid(value: str) -> None:
    UUID(value)


def assert_is_utc_timestamp(value: str) -> None:
    assert value.endswith("Z")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    assert parsed.tzinfo is not None
    assert parsed.utcoffset() == UTC.utcoffset(parsed)
