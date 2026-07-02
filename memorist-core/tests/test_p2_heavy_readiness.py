from __future__ import annotations

from pathlib import Path

import pytest

from memcore.governance.privacy import PrivacyService
from memcore.imports.fixtures import generate_openwebui_heavy_fixture
from memcore.imports.service import ImportService
from memcore.models import CreatorType, MessageRole
from memcore.privacy.residue_check import run_forget_residue_check
from memcore.repositories import MessageRepository, SessionRepository, WorkspaceRepository
from memcore.storage.commands import DEFAULT_MAX_WRITE_WEIGHT, FunctionWriteCommand, WriteResult
from memcore.storage.migrations import apply_migrations
from memcore.storage.sqlite import connect
from memcore.storage.write_actor import get_write_actor
from memcore.storage.write_commands.import_commands import commit_import_via_actor
from memcore.storage.write_commands.privacy_commands import run_privacy_request_command


def test_import_commit_runs_in_actor_batches(tmp_path: Path) -> None:
    db_path = tmp_path / "memorist.sqlite"
    object_store = tmp_path / "objects"
    fixture = tmp_path / "heavy.zip"
    generate_openwebui_heavy_fixture(fixture, conversations=8, messages_per_conversation=2)
    connection = connect(db_path)
    try:
        apply_migrations(connection)
        service = ImportService(connection, str(object_store))
        run = service.upload(str(fixture), mode="inspect")
        service.inspect(run["import_run_uuid"])
        service.reconstruct(run["import_run_uuid"], "openwebui")
        service.dry_run(run["import_run_uuid"])
        result = commit_import_via_actor(
            db_path,
            str(object_store),
            run["import_run_uuid"],
            batch_size=3,
        )
        assert result["status"] == "committed"
        assert result["imported_conversations"] == 8
        assert result["imported_messages"] == 16
    finally:
        connection.close()
        get_write_actor(db_path).stop_sync()


def test_write_actor_rejects_oversized_command(tmp_path: Path) -> None:
    db_path = tmp_path / "memorist.sqlite"
    actor = get_write_actor(db_path)
    command = FunctionWriteCommand(
        command_type="oversized-test",
        handler=lambda _connection: WriteResult(
            command_type="oversized-test",
            target_type=None,
            target_uuid=None,
            result={"should_not_run": True},
        ),
        estimated_write_weight=DEFAULT_MAX_WRITE_WEIGHT + 1,
    )
    try:
        with pytest.raises(ValueError, match="exceeds maximum write weight"):
            actor.submit_sync(command, timeout=5)
    finally:
        actor.stop_sync()


def test_forget_residue_check_passes_after_message_erasure(tmp_path: Path) -> None:
    marker = "P2_TEST_RESIDUE_MARKER"
    db_path = tmp_path / "memorist.sqlite"
    connection = connect(db_path)
    try:
        apply_migrations(connection)
        workspace = WorkspaceRepository(connection).create_workspace("Residue Test")
        session = SessionRepository(connection).create_session(
            workspace_uuid=workspace.workspace_uuid,
            title="Residue Test",
        )
        message = MessageRepository(connection).create_message(
            session.session_uuid,
            MessageRole.USER,
            CreatorType.USER,
            raw_text=f"erase this marker {marker}",
            raw_payload={"marker": marker},
            snapshot={"marker": marker},
        )
        preview = PrivacyService(connection).preview_request(
            request_type="forget",
            target_type="message",
            target_uuid=message.message_uuid,
            requested_scope={"test": True},
            actor_type="user",
        )
        run_privacy_request_command(
            db_path,
            preview["privacy_request_uuid"],
            "confirm",
            confirmation_value=preview["confirmation_token"],
        )
        run_privacy_request_command(db_path, preview["privacy_request_uuid"], "execute")
        residue = run_forget_residue_check(connection, message.message_uuid, marker)
        assert residue["status"] == "clean"
    finally:
        connection.close()
        get_write_actor(db_path).stop_sync()
