from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from zipfile import ZipFile

import pytest

from memcore.config import Settings
from memcore.imports.processing import ImportMessageProcessor
from memcore.imports.runtime import import_connection, initialize_runtime_storage
from memcore.imports.service import ImportService
from memcore.imports.worker import ImportReconstructionWorkerService
from memcore.memory_worker.pipeline import MemoryWorkerPipeline


def _settings(tmp_path: Path, *, concurrency: int = 2) -> Settings:
    return Settings(
        runtime_profile="lite",
        canonical_store="sqlite",
        db_path=str(tmp_path / "memorist.sqlite3"),
        object_store_path=str(tmp_path / "objects"),
        import_reconstruction_worker_enabled=True,
        import_reconstruction_concurrency=concurrency,
        import_reconstruction_poll_seconds=1,
        import_reconstruction_lease_seconds=3,
        import_reconstruction_heartbeat_seconds=1,
    )


def _archive(path: Path, messages: int = 4) -> Path:
    mapping: dict[str, object] = {
        "root": {"id": "root", "message": None, "parent": None, "children": ["m0"]}
    }
    for index in range(messages):
        node_id = f"m{index}"
        next_ids = [f"m{index + 1}"] if index + 1 < messages else []
        mapping[node_id] = {
            "id": node_id,
            "parent": "root" if index == 0 else f"m{index - 1}",
            "children": next_ids,
            "message": {
                "id": node_id,
                "author": {"role": "user" if index % 2 == 0 else "assistant"},
                "create_time": 1_700_000_000 + index,
                "content": {"content_type": "text", "parts": [f"Durable fact {index}."]},
            },
        }
    archive = path / "chatgpt.zip"
    with ZipFile(archive, "w") as zip_file:
        zip_file.writestr(
            "conversations.json",
            json.dumps(
                [
                    {
                        "id": "worker-lifecycle",
                        "title": "Worker lifecycle",
                        "create_time": 1_700_000_000,
                        "mapping": mapping,
                    }
                ]
            ),
        )
    return archive


def _commit(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, messages: int = 4
) -> str:
    import memcore.imports.service as service_module

    monkeypatch.setattr(service_module, "get_settings", lambda: settings)
    initialize_runtime_storage(settings)
    with import_connection(settings) as connection:
        service = ImportService(connection, settings.object_store_path)
        run = service.upload(str(_archive(tmp_path, messages)))
        run_uuid = str(run["import_run_uuid"])
        service.inspect(run_uuid)
        service.reconstruct(run_uuid)
        service.dry_run(run_uuid, "full_memory_reconstruction")
        service.commit(run_uuid, "full_memory_reconstruction")
        return run_uuid


def _wait_terminal(settings: Settings, run_uuid: str, timeout: float = 12.0) -> dict[str, object]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with import_connection(settings) as connection:
            run = ImportService(connection, settings.object_store_path).repository.get_run(run_uuid)
            if run["status"] in {"fully_reconstructed", "completed_with_failures", "cancelled"}:
                return run
        time.sleep(0.05)
    raise AssertionError(f"import {run_uuid} did not become terminal before timeout")


def test_lite_background_worker_start_stop_completes_without_process_endpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    run_uuid = _commit(settings, tmp_path, monkeypatch)
    worker = ImportReconstructionWorkerService(settings)
    worker.start()
    try:
        final = _wait_terminal(settings, run_uuid)
    finally:
        worker.stop()
    assert final["status"] == "fully_reconstructed"
    assert worker._threads == []


def test_pause_blocks_claims_and_resume_continues_automatically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    run_uuid = _commit(settings, tmp_path, monkeypatch)
    with import_connection(settings) as connection:
        service = ImportService(connection, settings.object_store_path)
        service.pause(run_uuid)
    worker = ImportReconstructionWorkerService(settings)
    worker.start()
    try:
        time.sleep(1.1)
        with import_connection(settings) as connection:
            statuses = ImportService(
                connection, settings.object_store_path
            ).message_processing_statuses(run_uuid)
            assert any(item["status"] == "queued" for item in statuses)
            ImportService(connection, settings.object_store_path).resume(run_uuid)
        final = _wait_terminal(settings, run_uuid)
    finally:
        worker.stop()
    assert final["status"] == "fully_reconstructed"


def test_two_worker_threads_do_not_duplicate_processing_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path, concurrency=2)
    run_uuid = _commit(settings, tmp_path, monkeypatch, messages=8)
    worker = ImportReconstructionWorkerService(settings)
    worker.start()
    try:
        assert _wait_terminal(settings, run_uuid)["status"] == "fully_reconstructed"
    finally:
        worker.stop()
    with import_connection(settings) as connection:
        duplicate_identities = connection.execute(
            """
            SELECT processing_identity_hash, COUNT(*) AS copies
            FROM memory_processing_runs
            GROUP BY processing_identity_hash
            HAVING COUNT(*) > 1
            """
        ).fetchall()
        duplicate_candidates = connection.execute(
            """
            SELECT processing_run_uuid, text_unit_uuid, candidate_type, COUNT(*) AS copies
            FROM memory_candidates
            GROUP BY processing_run_uuid, text_unit_uuid, candidate_type
            HAVING COUNT(*) > 1
            """
        ).fetchall()
    assert duplicate_identities == []
    assert duplicate_candidates == []


def test_slow_provider_heartbeat_prevents_a_second_worker_from_stealing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path, concurrency=1)
    run_uuid = _commit(settings, tmp_path, monkeypatch, messages=1)
    original = MemoryWorkerPipeline.process_message
    entered = threading.Event()

    def slow_process(
        self: MemoryWorkerPipeline,
        message_uuid: str,
        import_run_uuid: str | None = None,
        job_uuid: str | None = None,
        model_target: dict[str, object] | None = None,
    ) -> dict[str, object]:
        entered.set()
        time.sleep(2.2)
        return original(self, message_uuid, import_run_uuid, job_uuid, model_target)

    monkeypatch.setattr(MemoryWorkerPipeline, "process_message", slow_process)
    worker = ImportReconstructionWorkerService(settings)
    thread = threading.Thread(
        target=lambda: worker.process_one_claimed_item(worker_id="slow-worker"), daemon=True
    )
    thread.start()
    assert entered.wait(timeout=3)
    time.sleep(1.2)
    with import_connection(settings) as connection:
        running = connection.execute(
            """
            SELECT heartbeat_at FROM import_message_processing_status
            WHERE import_run_uuid = ? AND status = 'running'
            """,
            (run_uuid,),
        ).fetchone()
    assert running is not None and running["heartbeat_at"] is not None
    assert (
        worker.process_one_claimed_item(
            worker_id="would-be-thief",
            import_run_uuid=run_uuid,
        )
        is False
    )
    thread.join(timeout=8)
    assert not thread.is_alive()


def test_lost_lease_owner_cannot_process_or_overwrite_reclaimed_item(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path, concurrency=1)
    run_uuid = _commit(settings, tmp_path, monkeypatch, messages=1)
    with import_connection(settings) as connection:
        processor = ImportMessageProcessor(connection, settings)
        claimed = processor.claim_next("worker-a", 3, run_uuid)
        assert claimed is not None
        with connection:
            connection.execute(
                """
                UPDATE import_message_processing_status
                SET lease_owner = 'worker-b'
                WHERE status_uuid = ?
                """,
                (claimed["status_uuid"],),
            )

        def must_not_run(*_args: object, **_kwargs: object) -> dict[str, object]:
            raise AssertionError("lost lease owner executed the pipeline")

        monkeypatch.setattr(MemoryWorkerPipeline, "process_message", must_not_run)
        processor._process_one(claimed)
        status = connection.execute(
            """
            SELECT status, lease_owner FROM import_message_processing_status
            WHERE status_uuid = ?
            """,
            (claimed["status_uuid"],),
        ).fetchone()
        job = connection.execute(
            "SELECT status FROM jobs WHERE job_uuid = ?", (claimed["job_uuid"],)
        ).fetchone()
    assert dict(status) == {"status": "running", "lease_owner": "worker-b"}
    assert job["status"] == "pending"


def test_restart_recovery_finishes_remaining_queue_without_duplicates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path, concurrency=1)
    run_uuid = _commit(settings, tmp_path, monkeypatch, messages=4)
    original = MemoryWorkerPipeline.process_message
    entered = threading.Event()

    def first_slow(
        self: MemoryWorkerPipeline,
        message_uuid: str,
        import_run_uuid: str | None = None,
        job_uuid: str | None = None,
        model_target: dict[str, object] | None = None,
    ) -> dict[str, object]:
        entered.set()
        time.sleep(0.3)
        return original(self, message_uuid, import_run_uuid, job_uuid, model_target)

    monkeypatch.setattr(MemoryWorkerPipeline, "process_message", first_slow)
    first = ImportReconstructionWorkerService(settings)
    first.start()
    assert entered.wait(timeout=3)
    first.stop()
    monkeypatch.setattr(MemoryWorkerPipeline, "process_message", original)

    second = ImportReconstructionWorkerService(settings)
    second.start()
    try:
        assert _wait_terminal(settings, run_uuid)["status"] == "fully_reconstructed"
    finally:
        second.stop()
    with import_connection(settings) as connection:
        duplicates = connection.execute(
            """
            SELECT processing_identity_hash, COUNT(*) AS copies
            FROM memory_processing_runs GROUP BY processing_identity_hash
            HAVING COUNT(*) > 1
            """
        ).fetchall()
    assert duplicates == []


def test_cancel_revokes_claim_and_late_worker_cannot_overwrite_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path, concurrency=1)
    run_uuid = _commit(settings, tmp_path, monkeypatch, messages=1)
    with import_connection(settings) as connection:
        processor = ImportMessageProcessor(connection, settings)
        claimed = processor.claim_next("cancelled-worker", 3, run_uuid)
        assert claimed is not None
        ImportService(connection, settings.object_store_path).cancel(run_uuid)

        def must_not_run(*_args: object, **_kwargs: object) -> dict[str, object]:
            raise AssertionError("cancelled worker executed the pipeline")

        monkeypatch.setattr(MemoryWorkerPipeline, "process_message", must_not_run)
        processor._process_one(claimed)
        status = connection.execute(
            """
            SELECT status, lease_owner FROM import_message_processing_status
            WHERE status_uuid = ?
            """,
            (claimed["status_uuid"],),
        ).fetchone()
        job = connection.execute(
            "SELECT status FROM jobs WHERE job_uuid = ?", (claimed["job_uuid"],)
        ).fetchone()
    assert dict(status) == {"status": "cancelled", "lease_owner": None}
    assert job["status"] == "cancelled"
