from __future__ import annotations

import threading
from pathlib import Path

import pytest

from memcore.imports.processing import ImportMessageProcessor
from memcore.imports.runtime import import_connection
from memcore.imports.service import ImportService
from memcore.imports.worker import ImportReconstructionWorkerService
from memcore.memory_worker.pipeline import MemoryWorkerPipeline
from memcore.memory_worker.prepared import PreparedJakobsonInference
from memcore.model_control.repository import ModelControlRepository
from memcore.model_control.schemas import UsageEventCreate
from memcore.models import ModelRole
from memcore.repositories import JobRepository
from test_import_worker_lifecycle import _commit, _settings, _wait_terminal


def test_expired_lease_recovery_requeues_locked_job_atomically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path, concurrency=1)
    run_uuid = _commit(settings, tmp_path, monkeypatch, messages=1)
    original = MemoryWorkerPipeline.prepare_message
    entered = threading.Event()
    release = threading.Event()

    def blocked_prepare(
        self: MemoryWorkerPipeline,
        message_uuid: str,
        model_target: dict[str, object] | None = None,
    ) -> PreparedJakobsonInference:
        entered.set()
        assert release.wait(timeout=8)
        return original(self, message_uuid, model_target)

    monkeypatch.setattr(MemoryWorkerPipeline, "prepare_message", blocked_prepare)
    worker = ImportReconstructionWorkerService(settings)
    late = threading.Thread(
        target=lambda: worker.process_one_claimed_item(
            worker_id="expired-worker", import_run_uuid=run_uuid, heartbeat=False
        ),
        daemon=True,
    )
    late.start()
    assert entered.wait(timeout=3)

    with import_connection(settings) as connection:
        running = connection.execute(
            """
            SELECT status_uuid, job_uuid
            FROM import_message_processing_status
            WHERE import_run_uuid = ? AND status = 'running'
            """,
            (run_uuid,),
        ).fetchone()
        assert running is not None
        job_before = connection.execute(
            "SELECT status, locked_by FROM jobs WHERE job_uuid = ?",
            (running["job_uuid"],),
        ).fetchone()
        assert dict(job_before) == {"status": "running", "locked_by": "expired-worker"}
        connection.execute(
            """
            UPDATE import_message_processing_status
            SET lease_expires_at = '2000-01-01T00:00:00'
            WHERE status_uuid = ?
            """,
            (running["status_uuid"],),
        )
        connection.commit()
        assert ImportMessageProcessor(connection, settings).recover_expired_leases() == 1
        recovered = connection.execute(
            """
            SELECT status, lease_owner, processing_attempt_uuid
            FROM import_message_processing_status WHERE status_uuid = ?
            """,
            (running["status_uuid"],),
        ).fetchone()
        job_after = connection.execute(
            "SELECT status, locked_by, locked_at FROM jobs WHERE job_uuid = ?",
            (running["job_uuid"],),
        ).fetchone()
    assert dict(recovered) == {
        "status": "queued",
        "lease_owner": None,
        "processing_attempt_uuid": None,
    }
    assert dict(job_after) == {"status": "pending", "locked_by": None, "locked_at": None}

    release.set()
    late.join(timeout=8)
    assert not late.is_alive()


def test_pause_during_inference_requeues_claim_and_job_without_waiting_for_expiry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path, concurrency=1)
    run_uuid = _commit(settings, tmp_path, monkeypatch, messages=1)
    original = MemoryWorkerPipeline.prepare_message
    entered = threading.Event()
    release = threading.Event()

    def blocked_prepare(
        self: MemoryWorkerPipeline,
        message_uuid: str,
        model_target: dict[str, object] | None = None,
    ) -> PreparedJakobsonInference:
        entered.set()
        assert release.wait(timeout=8)
        return original(self, message_uuid, model_target)

    monkeypatch.setattr(MemoryWorkerPipeline, "prepare_message", blocked_prepare)
    worker = ImportReconstructionWorkerService(settings)
    late = threading.Thread(
        target=lambda: worker.process_one_claimed_item(
            worker_id="paused-worker", import_run_uuid=run_uuid, heartbeat=False
        ),
        daemon=True,
    )
    late.start()
    assert entered.wait(timeout=3)
    with import_connection(settings) as connection:
        ImportService(connection, settings.object_store_path).pause(run_uuid)
    release.set()
    late.join(timeout=8)
    assert not late.is_alive()

    with import_connection(settings) as connection:
        status = connection.execute(
            """
            SELECT status, lease_owner, processing_attempt_uuid, job_uuid
            FROM import_message_processing_status
            WHERE import_run_uuid = ? AND skip_reason IS NULL
            """,
            (run_uuid,),
        ).fetchone()
        job = connection.execute(
            "SELECT status, locked_by, locked_at FROM jobs WHERE job_uuid = ?",
            (status["job_uuid"],),
        ).fetchone()
        artifact_counts = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "memory_processing_runs",
                "prompt_execution_runs",
                "jakobson_analysis_runs",
                "memory_candidates",
                "memory_versions",
                "graph_projection_outbox",
            )
        }
    assert status["status"] == "queued"
    assert status["lease_owner"] is None
    assert status["processing_attempt_uuid"] is None
    assert dict(job) == {"status": "pending", "locked_by": None, "locked_at": None}
    assert artifact_counts == {
        "memory_processing_runs": 0,
        "prompt_execution_runs": 0,
        "jakobson_analysis_runs": 0,
        "memory_candidates": 0,
        "memory_versions": 0,
        "graph_projection_outbox": 0,
    }

    with import_connection(settings) as connection:
        ImportService(connection, settings.object_store_path).resume(run_uuid)
    monkeypatch.setattr(MemoryWorkerPipeline, "prepare_message", original)
    assert worker.process_one_claimed_item(worker_id="resumed-worker", import_run_uuid=run_uuid)
    assert _wait_terminal(settings, run_uuid)["status"] == "fully_reconstructed"


def test_processing_report_provider_tokens_use_execution_events_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path, concurrency=1)
    run_uuid = _commit(settings, tmp_path, monkeypatch, messages=1)
    worker = ImportReconstructionWorkerService(settings)
    assert worker.process_one_claimed_item(
        worker_id="report-token-worker", import_run_uuid=run_uuid
    )
    with import_connection(settings) as connection:
        processor = ImportMessageProcessor(connection, settings)
        before = processor.processing_report(run_uuid)
        import_row_before = next(
            row
            for row in before["provider_breakdown"]
            if row["operational_role"] == "import_reconstruction" and row["processing_jobs"] > 0
        )
        unrelated = JobRepository(connection).enqueue_job_once(
            "unrelated_usage_audit",
            {"import_run_uuid": run_uuid, "purpose": "report-contract"},
        )
        ModelControlRepository(connection).record_usage_event(
            UsageEventCreate(
                role=ModelRole.IMPORT_RECONSTRUCTION,
                stage="unrelated_internal_accounting",
                provider_type=str(import_row_before["provider_type"]),
                model_name=str(import_row_before["model_name"]),
                import_run_uuid=run_uuid,
                job_uuid=unrelated.job_uuid,
                input_tokens=997,
                output_tokens=991,
                status="ok",
            )
        )
        after = processor.processing_report(run_uuid)
    import_row_after = next(
        row
        for row in after["provider_breakdown"]
        if row["operational_role"] == "import_reconstruction" and row["processing_jobs"] > 0
    )
    assert import_row_after["input_tokens"] == import_row_before["input_tokens"]
    assert import_row_after["output_tokens"] == import_row_before["output_tokens"]
