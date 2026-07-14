import sqlite3
from collections.abc import Generator, Sequence
from pathlib import Path

import pytest

from memcore.config import Settings
from memcore.memory_worker.contracts import CheapGateClassifier, GateResult
from memcore.memory_worker.gating import DeterministicGate
from memcore.memory_worker.pipeline import MemoryWorkerPipeline
from memcore.memory_worker.unitizer import DeterministicUnitizer
from memcore.models import GateDecisionValue, MessageRole, ProcessingStatus
from memcore.repositories import (
    MessageRepository,
    ProjectRepository,
    SessionRepository,
    WorkspaceRepository,
)
from memcore.repositories.memory_worker import MemoryStoreRepository
from memcore.storage.migrations import apply_migrations
from memcore.storage.sqlite import connect


@pytest.fixture()
def connection(tmp_path: Path) -> Generator[sqlite3.Connection, None, None]:
    sqlite_connection = connect(tmp_path / "phase2.sqlite")
    apply_migrations(sqlite_connection)
    try:
        yield sqlite_connection
    finally:
        sqlite_connection.close()


def test_phase2_migration_tables_and_indexes_exist(connection: sqlite3.Connection) -> None:
    tables = {
        row["name"]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    indexes = {
        row["name"]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index'"
        ).fetchall()
    }

    assert {
        "memory_processing_runs",
        "text_units",
        "memory_gate_decisions",
        "linguistic_analyses",
        "memory_candidates",
        "candidate_evidence",
        "memories",
        "memory_versions",
        "memory_evidence_links",
        "memory_relations",
        "memory_consolidation_decisions",
        "graph_projection_outbox",
    }.issubset(tables)
    assert {
        "idx_processing_run_idempotency",
        "idx_text_units_order",
        "idx_memory_candidate_idempotency",
        "idx_graph_outbox_idempotency",
    }.issubset(indexes)


def test_unitizer_handles_persian_english_lists_and_code_blocks() -> None:
    text = (
        "سلام. یادت باشد من جواب کوتاه را ترجیح می‌دهم.\n"
        "1. Keep Open WebUI branding.\n"
        "2. Do not use cloud storage.\n"
        "```python\nprint('hi. no split')\n```\n"
    )
    unitizer = DeterministicUnitizer()

    units = unitizer.unitize(text)

    assert [unit.unit_index for unit in units] == list(range(len(units)))
    assert any(unit.language_code == "fa" for unit in units)
    assert any(unit.unit_type.value == "list_item" for unit in units)
    assert any(unit.unit_type.value == "code_block" for unit in units)
    for unit in units:
        assert text[unit.start_char : unit.end_char] == unit.text
    assert [unit.model_dump() for unit in units] == [
        unit.model_dump() for unit in unitizer.unitize(text)
    ]


def test_gating_explicit_preference_greeting_and_classifier_fallback(
    connection: sqlite3.Connection,
) -> None:
    message = MessageRepository(connection).create_message(
        SessionRepository(connection).create_session().session_uuid,
        role="user",
        creator_type="user",
        raw_text="Hello. I prefer concise answers.",
    )
    units = DeterministicUnitizer().to_text_units(
        message.message_uuid,
        message.session_uuid,
        message.role.value,
        message.raw_text or "",
    )

    gate = DeterministicGate(classifier=FailingClassifier())
    greeting = gate.evaluate(units[0])
    preference = gate.evaluate(units[1])

    assert greeting.decision is GateDecisionValue.DISCARD
    assert preference.decision is GateDecisionValue.ANALYZE
    assert "model_classifier_failed" in preference.reason_codes


def test_complete_message_to_memory_path_is_local_and_evidence_grounded(
    connection: sqlite3.Connection,
    tmp_path: Path,
) -> None:
    settings = Settings(
        db_path=str(tmp_path / "phase2.sqlite"),
        object_store_path=str(tmp_path / "objects"),
    )
    workspace = WorkspaceRepository(connection).create_workspace("Workspace")
    project = ProjectRepository(connection).create_project(workspace.workspace_uuid, "Project")
    session = SessionRepository(connection).create_session(
        workspace_uuid=workspace.workspace_uuid,
        project_uuid=project.project_uuid,
    )
    message = MessageRepository(connection).create_message(
        session.session_uuid,
        role=MessageRole.USER,
        creator_type="user",
        raw_text="I prefer concise answers.",
    )

    result = MemoryWorkerPipeline(connection, settings).process_message(message.message_uuid)

    updated_message = MessageRepository(connection).get_message(message.message_uuid)
    memories = MemoryStoreRepository(connection).list_memories()
    evidence = MemoryStoreRepository(connection).list_evidence(memories[0].memory_uuid)
    outbox = connection.execute("SELECT * FROM graph_projection_outbox").fetchall()

    assert result["candidates"] == 1
    assert updated_message is not None
    assert updated_message.processing_status is ProcessingStatus.AVAILABLE
    assert len(memories) == 1
    assert evidence[0]["evidence_text"] == "I prefer concise answers."
    assert outbox[0]["status"] == "succeeded"


def test_repeated_and_updated_preferences_are_versioned(
    connection: sqlite3.Connection,
    tmp_path: Path,
) -> None:
    settings = Settings(
        db_path=str(tmp_path / "phase2.sqlite"),
        object_store_path=str(tmp_path / "objects"),
    )
    session = SessionRepository(connection).create_session()
    messages = MessageRepository(connection)
    pipeline = MemoryWorkerPipeline(connection, settings)

    first = messages.create_message(
        session.session_uuid,
        role="user",
        creator_type="user",
        raw_text="I prefer concise answers.",
    )
    repeated = messages.create_message(
        session.session_uuid,
        role="user",
        creator_type="user",
        raw_text="I prefer concise answers.",
    )
    updated = messages.create_message(
        session.session_uuid,
        role="user",
        creator_type="user",
        raw_text="I prefer detailed answers.",
    )

    pipeline.process_message(first.message_uuid)
    pipeline.process_message(repeated.message_uuid)
    pipeline.process_message(updated.message_uuid)

    store = MemoryStoreRepository(connection)
    memories = store.list_memories()
    versions = store.list_versions(memories[0].memory_uuid)
    decisions = connection.execute(
        "SELECT operation FROM memory_consolidation_decisions ORDER BY created_at"
    ).fetchall()

    assert len(memories) == 1
    assert [version.version_number for version in versions] == [1, 2]
    assert [row["operation"] for row in decisions] == ["ADD", "REINFORCE", "SUPERSEDE"]
    assert versions[0].transaction_until is not None


def test_assistant_speculation_and_forget_request_do_not_create_memory(
    connection: sqlite3.Connection,
    tmp_path: Path,
) -> None:
    settings = Settings(
        db_path=str(tmp_path / "phase2.sqlite"),
        object_store_path=str(tmp_path / "objects"),
    )
    session = SessionRepository(connection).create_session()
    messages = MessageRepository(connection)
    assistant = messages.create_message(
        session.session_uuid,
        role="assistant",
        creator_type="model",
        raw_text="The user prefers cloud storage.",
    )
    forget = messages.create_message(
        session.session_uuid,
        role="user",
        creator_type="user",
        raw_text="Forget this preference.",
    )

    pipeline = MemoryWorkerPipeline(connection, settings)
    pipeline.process_message(assistant.message_uuid)
    pipeline.process_message(forget.message_uuid)

    memories = MemoryStoreRepository(connection).list_memories()
    operations = {
        row["operation"]
        for row in connection.execute("SELECT operation FROM memory_consolidation_decisions")
    }

    assert memories == []
    forget_gate = connection.execute(
        """
        SELECT gd.decision
        FROM memory_gate_decisions gd
        JOIN text_units tu ON tu.text_unit_uuid = gd.text_unit_uuid
        WHERE tu.message_uuid = ?
        """,
        (forget.message_uuid,),
    ).fetchone()
    assert forget_gate is not None
    assert forget_gate["decision"] == GateDecisionValue.MANUAL_REVIEW.value
    assert operations == {"REJECT"}


class FailingClassifier:
    def classify(self, text: str, reason_codes: Sequence[str]) -> GateResult | None:
        raise RuntimeError("classifier unavailable")


def assert_classifier_protocol(_: CheapGateClassifier) -> None:
    return None


assert_classifier_protocol(FailingClassifier())
