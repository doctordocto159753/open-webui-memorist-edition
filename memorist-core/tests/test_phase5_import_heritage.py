import json
import sqlite3
import zipfile
from collections.abc import Generator
from pathlib import Path

import pytest

from memcore.heritage.package import export_heritage, restore_heritage, verify_heritage
from memcore.imports.adapters.chatgpt import ChatGPTAdapter
from memcore.imports.adapters.claude import ClaudeAdapter
from memcore.imports.adapters.gemini import GeminiAdapter
from memcore.imports.models import ImportAdapter
from memcore.imports.security import ImportSecurityError, validate_zip_archive
from memcore.imports.service import ImportService
from memcore.repositories import MessageRepository, SessionRepository, WorkspaceRepository
from memcore.storage.migrations import apply_migrations
from memcore.storage.sqlite import connect
from memcore.validators.ijson import load_ijson


@pytest.fixture()
def connection(tmp_path: Path) -> Generator[sqlite3.Connection, None, None]:
    sqlite_connection = connect(tmp_path / "phase5.sqlite")
    apply_migrations(sqlite_connection)
    try:
        yield sqlite_connection
    finally:
        sqlite_connection.close()


def test_phase5_migration_tables_exist(connection: sqlite3.Connection) -> None:
    tables = {
        row["name"]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table', 'virtual table')"
        )
    }

    assert {
        "import_runs",
        "import_artifacts",
        "import_records",
        "import_mappings",
        "import_issues",
        "imported_conversations",
        "import_dry_run_reports",
        "heritage_packages",
    }.issubset(tables)


def test_zip_slip_is_rejected(tmp_path: Path) -> None:
    archive_path = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("../evil.json", "{}")

    with pytest.raises(ImportSecurityError, match="path traversal"):
        validate_zip_archive(str(archive_path))


def test_openwebui_import_dry_run_commit_and_deduplication(
    connection: sqlite3.Connection,
    tmp_path: Path,
) -> None:
    archive_path = tmp_path / "openwebui.zip"
    payload = [
        {
            "id": "chat-1",
            "title": "Branched chat",
            "chat": {
                "history": {
                    "currentId": "m3",
                    "messages": {
                        "m1": {
                            "role": "user",
                            "content": "Hello",
                            "parentId": None,
                            "childrenIds": ["m2", "m2b"],
                            "timestamp": "2024-01-01T00:00:00Z",
                        },
                        "m2": {
                            "role": "assistant",
                            "content": "Hi",
                            "parentId": "m1",
                            "childrenIds": ["m3"],
                        },
                        "m2b": {
                            "role": "assistant",
                            "content": "Alternate",
                            "parentId": "m1",
                            "childrenIds": [],
                        },
                        "m3": {
                            "role": "user",
                            "content": "Continue",
                            "parentId": "m2",
                            "childrenIds": [],
                        },
                    },
                }
            },
        }
    ]
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("chat.json", json.dumps(payload))

    service = ImportService(connection, str(tmp_path / "objects"))
    run = service.upload(str(archive_path), mode="inspect")
    inspected = service.inspect(run["import_run_uuid"])
    reconstructed = service.reconstruct(run["import_run_uuid"])
    dry_run = service.dry_run(run["import_run_uuid"])
    commit = service.commit(run["import_run_uuid"])
    second_dry_run = service.dry_run(run["import_run_uuid"])

    sessions = connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    messages = connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    mappings = connection.execute("SELECT COUNT(*) FROM import_mappings").fetchone()[0]
    report = load_ijson(dry_run["report_ijson"])

    assert inspected["source_platform"] == "openwebui"
    assert reconstructed["total_conversations"] == 1
    assert report["decisions"][0]["branch_count"] == 1
    assert commit["imported_messages"] == 4
    assert sessions == 1
    assert messages == 4
    assert mappings == 5
    second_report = load_ijson(second_dry_run["report_ijson"])
    assert second_report["decisions"][0]["decision"] == "already_mapped"


def test_provider_adapters_probe_known_shapes(tmp_path: Path) -> None:
    chatgpt = _zip_json(
        tmp_path,
        "chatgpt.zip",
        [
            {
                "id": "c1",
                "mapping": {
                    "n1": {
                        "id": "n1",
                        "parent": None,
                        "children": [],
                        "message": {
                            "author": {"role": "assistant"},
                            "content": {"parts": ["hello"]},
                        },
                    }
                },
            }
        ],
    )
    claude = _zip_json(
        tmp_path,
        "claude.zip",
        [{"uuid": "c1", "chat_messages": [{"sender": "human", "text": "hello"}]}],
    )
    gemini = _zip_json(
        tmp_path,
        "gemini.zip",
        [{"time": "2024-01-01T00:00:00Z", "title": "Gemini Apps"}],
    )

    cases: list[tuple[Path, ImportAdapter, str]] = [
        (chatgpt, ChatGPTAdapter(), "chatgpt_conversation_mapping"),
        (claude, ClaudeAdapter(), "claude_conversation_export"),
        (gemini, GeminiAdapter(), "google_takeout_gemini_activity"),
    ]
    for archive_path, adapter, expected in cases:
        conn = connect(":memory:")
        apply_migrations(conn)
        try:
            service = ImportService(conn, str(tmp_path / f"objects-{expected}"))
            run = service.upload(str(archive_path))
            artifacts = service._staged_artifacts(run["import_run_uuid"])
            assert adapter.probe(artifacts).detected_format == expected
        finally:
            conn.close()


def test_malformed_json_is_preserved_as_issue_free_artifact(
    connection: sqlite3.Connection,
    tmp_path: Path,
) -> None:
    archive_path = tmp_path / "malformed.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("broken.json", "{not json")

    service = ImportService(connection, str(tmp_path / "objects"))
    run = service.upload(str(archive_path))
    inspected = service.inspect(run["import_run_uuid"])
    artifacts = service.repository.list_artifacts(run["import_run_uuid"])

    assert inspected["status"] == "inspected"
    assert artifacts[0]["sha256"]


def test_heritage_export_verify_restore_round_trip(
    connection: sqlite3.Connection,
    tmp_path: Path,
) -> None:
    workspace = WorkspaceRepository(connection).create_workspace("Workspace")
    session = SessionRepository(connection).create_session(workspace_uuid=workspace.workspace_uuid)
    MessageRepository(connection).create_message(
        session.session_uuid,
        role="user",
        creator_type="user",
        raw_text="Hello",
    )
    package_path = tmp_path / "heritage.zip"
    restored_db = tmp_path / "restored.sqlite"

    exported = export_heritage(connection, package_path)
    verification = verify_heritage(package_path)
    dry_run = restore_heritage(package_path, restored_db, dry_run=True)
    restored = restore_heritage(package_path, restored_db, dry_run=False)

    assert exported["verification"]["valid"] is True
    assert verification["valid"] is True
    assert dry_run["record_counts"]["messages"] == 1
    assert restored["status"] == "restored"
    restored_connection = connect(restored_db)
    try:
        assert restored_connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 1
    finally:
        restored_connection.close()


def test_heritage_checksum_detects_modification(
    connection: sqlite3.Connection,
    tmp_path: Path,
) -> None:
    package_path = tmp_path / "heritage.zip"
    export_heritage(connection, package_path)
    tampered = tmp_path / "tampered.zip"
    with zipfile.ZipFile(package_path) as source, zipfile.ZipFile(tampered, "w") as target:
        for name in source.namelist():
            data = source.read(name)
            if name.endswith("manifest.ijson"):
                data = data.replace(b"memorist-heritage", b"memorist-tampered")
            target.writestr(name, data)

    assert verify_heritage(tampered)["valid"] is False


def _zip_json(tmp_path: Path, name: str, payload: object) -> Path:
    archive_path = tmp_path / name
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("data.json", json.dumps(payload))
    return archive_path
