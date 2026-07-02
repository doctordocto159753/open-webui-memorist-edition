from __future__ import annotations

import sqlite3
from pathlib import Path

from memcore.config import Settings
from memcore.full_mode import build_full_mode_status
from memcore.graph.projector import FalkorGraphProjector
from memcore.graph.service import GraphProjectionService
from memcore.imports.commit_batches import ImportCommitBatchRepository
from memcore.migrate.sqlite_to_postgres import dry_run
from memcore.scheduler.hot_scheduler import HotScheduler, HotSchedulerJob
from memcore.scheduler.lanes import SchedulerLane
from memcore.storage.canonical.factory import create_canonical_store
from memcore.storage.migrations import apply_migrations
from memcore.storage.postgres.jobs import CLAIM_NEXT_JOB_SQL, RECOVER_STALE_JOBS_SQL
from memcore.storage.postgres.outbox import claim_outbox_sql, recover_outbox_sql
from memcore.storage.postgres.parity import build_parity_report
from memcore.storage.sqlite import connect


def test_store_factory_selects_sqlite_by_default(tmp_path: Path) -> None:
    settings = Settings(db_path=str(tmp_path / "memorist.sqlite"))
    store = create_canonical_store(settings)

    assert store.store_kind == "sqlite"
    assert store.capabilities().full_text_search is True
    assert store.capabilities().skip_locked is False


def test_store_factory_exposes_postgres_capabilities(tmp_path: Path) -> None:
    settings = Settings(
        runtime_profile="full",
        canonical_store="postgres",
        postgres_dsn="postgresql://memorist:secret@localhost/memorist",
        graph_backend="falkordb",
        falkordb_url="redis://localhost:6379",
        hot_scheduler="in_memory",
        object_store_path=str(tmp_path / "objects"),
    )
    store = create_canonical_store(settings)

    assert store.store_kind == "postgres"
    assert store.capabilities().concurrent_writers is True
    assert store.capabilities().skip_locked is True
    assert store.capabilities().json_native is True


def test_postgres_schema_parity_report_passes_for_required_full_tables() -> None:
    report = build_parity_report()

    assert report["status"] == "pass"
    assert "jakobson_sentence_annotations" in report["postgres_tables"]
    assert "memory_signal_routes" in report["postgres_tables"]
    assert "graph_projection_outbox" in report["postgres_tables"]
    assert "embedding_outbox" in report["postgres_tables"]


def test_postgres_job_claim_uses_skip_locked() -> None:
    normalized = " ".join(CLAIM_NEXT_JOB_SQL.split()).lower()

    assert "for update skip locked" in normalized
    assert "order by priority desc" in normalized
    assert "returning *" in normalized
    assert "locked_by" in normalized


def test_postgres_stale_job_recovery_marks_retry_or_dead() -> None:
    normalized = " ".join(RECOVER_STALE_JOBS_SQL.split()).lower()

    assert "attempts >= max_attempts" in normalized
    assert "status = 'running'" in normalized
    assert "locked_at <" in normalized


def test_postgres_outbox_claim_and_recovery_use_skip_locked() -> None:
    claim = " ".join(claim_outbox_sql("graph_projection_outbox").split()).lower()
    recover = " ".join(recover_outbox_sql("graph_projection_outbox").split()).lower()

    assert "for update skip locked" in claim
    assert "status in ('pending', 'retry')" in claim
    assert "locked_at <" in recover


def test_hot_scheduler_preempts_import_after_low_priority_batch() -> None:
    scheduler = HotScheduler(max_consecutive_low_priority=1)
    scheduler.submit(
        HotSchedulerJob(
            durable_job_uuid="import-1",
            lane=SchedulerLane.IMPORT_COMMIT,
        )
    )
    first = scheduler.claim_next("worker-1")
    assert first is not None
    assert first.lane is SchedulerLane.IMPORT_COMMIT
    scheduler.complete("worker-1")

    scheduler.submit(
        HotSchedulerJob(
            durable_job_uuid="import-2",
            lane=SchedulerLane.IMPORT_COMMIT,
        )
    )
    scheduler.submit(
        HotSchedulerJob(
            durable_job_uuid="live-1",
            lane=SchedulerLane.LIVE_CHAT_CAPTURE,
        )
    )
    second = scheduler.claim_next("worker-1")

    assert second is not None
    assert second.lane is SchedulerLane.LIVE_CHAT_CAPTURE


def test_privacy_lane_preempts_import() -> None:
    scheduler = HotScheduler(max_consecutive_low_priority=1)
    scheduler.submit(HotSchedulerJob("import-1", SchedulerLane.IMPORT_COMMIT))
    scheduler.submit(HotSchedulerJob("privacy-1", SchedulerLane.CRITICAL_PRIVACY))

    claimed = scheduler.claim_next("worker-1")

    assert claimed is not None
    assert claimed.lane is SchedulerLane.CRITICAL_PRIVACY


def test_import_commit_batch_records_are_idempotent(tmp_path: Path) -> None:
    connection = _sqlite(tmp_path)
    try:
        connection.execute(
            """
            INSERT INTO import_runs (
                import_run_uuid, archive_sha256, mode, status, options_ijson, created_at
            )
            VALUES ('run-1', 'hash', 'dry_run', 'committing', '{}', '2026-01-01T00:00:00Z')
            """
        )
        repository = ImportCommitBatchRepository(connection)
        records = [{"imported_conversation_uuid": "conversation-1"}]

        first = repository.start_batch("run-1", records)
        second = repository.start_batch("run-1", records)

        assert first["batch_uuid"] == second["batch_uuid"]
        assert repository.finish_batch(first["batch_uuid"])["status"] == "committed"
    finally:
        connection.close()


def test_falkordb_projection_query_contains_jakobson_nodes_and_edges() -> None:
    projector = FalkorGraphProjector(_RecordingGraphClient())
    query = projector.jakobson_annotation_query(
        {
            "annotation_uuid": "annotation-1",
            "message_uuid": "message-1",
            "unit_uuid": "unit-1",
            "dominant_function": "conative",
            "receiver_value": "AI",
            "context_value": "prompt policy",
            "code_value": "Persian",
        }
    )

    assert "JakobsonAnnotation" in query
    assert "CommunicativeFunction" in query
    assert "HAS_JAKOBSON_ANNOTATION" in query
    assert "HAS_DOMINANT_FUNCTION" in query


def test_falkordb_down_reports_degraded_fallback(tmp_path: Path) -> None:
    settings = Settings(
        runtime_profile="full",
        canonical_store="postgres",
        postgres_dsn="postgresql://memorist:secret@localhost/memorist",
        graph_backend="falkordb",
        falkordb_url="redis://127.0.0.1:1",
        hot_scheduler="in_memory",
        object_store_path=str(tmp_path / "objects"),
    )

    diagnostics = GraphProjectionService(settings).diagnostics()

    assert diagnostics["status"] == "degraded"
    assert diagnostics["fallback"] == "postgres_retrieval"
    assert diagnostics["source_of_truth"] is False


def test_full_mode_status_exposes_degraded_certification(tmp_path: Path) -> None:
    settings = Settings(
        runtime_profile="full",
        canonical_store="postgres",
        postgres_dsn="postgresql://memorist:secret@localhost/memorist",
        graph_backend="falkordb",
        falkordb_url="redis://127.0.0.1:1",
        hot_scheduler="in_memory",
        object_store_path=str(tmp_path / "objects"),
    )

    status = build_full_mode_status(settings)

    assert status == {
        "runtime_profile": "full",
        "canonical_store": "postgres",
        "graph_backend": "falkordb",
        "graph_status": "degraded",
        "scheduler": "in_memory",
        "full_mode_certification": "degraded",
    }


def test_graph_forget_residue_marks_node_erased() -> None:
    client = _RecordingGraphClient()
    projector = FalkorGraphProjector(client)

    projector.delete_erased_source("memory-1")

    assert "status = 'erased'" in client.queries[0]
    assert "text = null" in client.queries[0]


def test_full_projection_query_contains_expected_full_graph_nodes_and_edges() -> None:
    projector = FalkorGraphProjector(_RecordingGraphClient())
    query = projector.full_memory_record_query(
        {
            "workspace_uuid": "workspace-1",
            "project_uuid": "project-1",
            "session_uuid": "session-1",
            "message_uuid": "message-1",
            "unit_uuid": "unit-1",
            "annotation_uuid": "annotation-1",
            "route_uuid": "route-1",
            "candidate_uuid": "candidate-1",
            "memory_uuid": "memory-1",
            "memory_version_uuid": "version-1",
            "dominant_function": "conative",
            "receiver_value": "AI",
            "context_value": "workflow",
            "code_value": "English",
            "memory_status": "active",
            "version_status": "current",
            "memory_value": "Remember the workflow.",
        }
    )

    for token in [
        "Workspace",
        "Project",
        "Session",
        "MemorySignalRoute",
        "MemoryCandidate",
        "MemoryVersion",
        "IN_PROJECT",
        "ROUTES_TO",
        "DERIVED_FROM",
        "HAS_VERSION",
        "EVIDENCED_BY",
    ]:
        assert token in query


def test_sqlite_to_postgres_dry_run_reports_counts(tmp_path: Path) -> None:
    connection = _sqlite(tmp_path)
    try:
        connection.execute(
            """
            INSERT INTO workspaces (workspace_uuid, name, created_at)
            VALUES ('workspace-1', 'Workspace', '2026-01-01T00:00:00Z')
            """
        )
        connection.commit()
    finally:
        connection.close()

    report = dry_run(tmp_path / "memorist.sqlite")

    assert report["mode"] == "dry-run"
    assert report["counts"]["workspaces"] == 1
    assert report["backup_required"] is True


class _RecordingGraphClient:
    def __init__(self) -> None:
        self.queries: list[str] = []

    def graph_query(self, query: str) -> None:
        self.queries.append(query)


def _sqlite(tmp_path: Path) -> sqlite3.Connection:
    connection = connect(tmp_path / "memorist.sqlite")
    apply_migrations(connection)
    return connection
