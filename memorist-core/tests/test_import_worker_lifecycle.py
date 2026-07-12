from __future__ import annotations

import json
import sqlite3
import threading
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zipfile import ZipFile

import pytest

from memcore.api import routes_openwebui
from memcore.api.routes_openwebui import MessageCaptureRequest
from memcore.config import Settings
from memcore.imports.processing import ImportMessageProcessor
from memcore.imports.runtime import import_connection, initialize_runtime_storage
from memcore.imports.service import ImportService
from memcore.imports.worker import ImportReconstructionWorkerService
from memcore.memory_worker.pipeline import MemoryWorkerPipeline
from memcore.memory_worker.prepared import PreparedJakobsonInference
from memcore.memory_worker.prompts.registry import PromptExecutionRepository
from memcore.memory_worker.prompts.versions import (
    JAKOBSON_SENTENCE_ANALYSIS_PROMPT_ID,
    PROMPT_PACK_VERSION,
)
from memcore.models import ModelRole
from memcore.repositories import JobRepository


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


def _archive(path: Path, messages: int = 4, first_message_text: str | None = None) -> Path:
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
                "content": {
                    "content_type": "text",
                    "parts": [
                        first_message_text
                        if index == 0 and first_message_text is not None
                        else f"Durable fact {index}."
                    ],
                },
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
    settings: Settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    messages: int = 4,
    first_message_text: str | None = None,
) -> str:
    import memcore.imports.service as service_module

    monkeypatch.setattr(service_module, "get_settings", lambda: settings)
    initialize_runtime_storage(settings)
    with import_connection(settings) as connection:
        service = ImportService(connection, settings.object_store_path)
        run = service.upload(
            str(_archive(tmp_path, messages, first_message_text=first_message_text))
        )
        run_uuid = str(run["import_run_uuid"])
        service.inspect(run_uuid)
        service.reconstruct(run_uuid)
        service.dry_run(run_uuid, "full_memory_reconstruction")
        service.commit(run_uuid, "full_memory_reconstruction")
        return run_uuid


def _wait_terminal(settings: Settings, run_uuid: str, timeout: float = 30.0) -> dict[str, object]:
    deadline = time.monotonic() + timeout
    last_run: dict[str, object] | None = None
    last_statuses: list[dict[str, object]] = []
    last_jobs: list[dict[str, object]] = []
    while time.monotonic() < deadline:
        with import_connection(settings) as connection:
            run = ImportService(connection, settings.object_store_path).repository.get_run(run_uuid)
            last_run = dict(run)
            if run["status"] in {"fully_reconstructed", "completed_with_failures", "cancelled"}:
                return run
            last_statuses = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT status_uuid, status, processing_stage, lease_owner,
                           processing_attempt_uuid, retry_count, run_after, error_sanitized
                    FROM import_message_processing_status
                    WHERE import_run_uuid = ?
                    ORDER BY created_at, status_uuid
                    """,
                    (run_uuid,),
                ).fetchall()
            ]
            last_jobs = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT job_uuid, status, attempts, locked_by, run_after, last_error_sanitized
                    FROM jobs
                    WHERE payload_ijson LIKE ?
                    ORDER BY created_at, job_uuid
                    """,
                    (f"%{run_uuid}%",),
                ).fetchall()
            ]
        time.sleep(0.05)
    raise AssertionError(
        f"import {run_uuid} did not become terminal before timeout; "
        f"run={last_run}; statuses={last_statuses}; jobs={last_jobs}"
    )


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
    original_claim = ImportMessageProcessor.claim_next
    claim_calls = {"count": 0}
    claim_lock = threading.Lock()

    def counted_claim(
        self: ImportMessageProcessor,
        worker_id: str,
        lease_seconds: int,
        import_run_uuid: str | None = None,
    ) -> dict[str, Any] | None:
        with claim_lock:
            claim_calls["count"] += 1
        return original_claim(self, worker_id, lease_seconds, import_run_uuid)

    monkeypatch.setattr(ImportMessageProcessor, "claim_next", counted_claim)
    worker = ImportReconstructionWorkerService(settings)
    worker.start()
    try:
        time.sleep(2.2)
        with import_connection(settings) as connection:
            statuses = ImportService(
                connection, settings.object_store_path
            ).message_processing_statuses(run_uuid)
            assert any(item["status"] == "queued" for item in statuses)
            assert not any(item["status"] == "running" for item in statuses)
            paused_claim_calls = claim_calls["count"]
            ImportService(connection, settings.object_store_path).resume(run_uuid)
        final = _wait_terminal(settings, run_uuid)
    finally:
        worker.stop()
    assert paused_claim_calls <= 6
    assert final["status"] == "fully_reconstructed"


def test_concurrent_operator_requests_use_distinct_request_owners_and_shared_fencing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path, concurrency=1)
    run_uuid = _commit(settings, tmp_path, monkeypatch, messages=4)
    original_claim = ImportMessageProcessor.claim_next
    owners: set[str] = set()
    claims_by_owner: dict[str, int] = {}
    owner_lock = threading.Lock()
    start = threading.Barrier(3)
    results: list[dict[str, object]] = []

    def recording_claim(
        self: ImportMessageProcessor,
        worker_id: str,
        lease_seconds: int,
        import_run_uuid: str | None = None,
    ) -> dict[str, Any] | None:
        if import_run_uuid == run_uuid:
            with owner_lock:
                owners.add(worker_id)
                claims_by_owner[worker_id] = claims_by_owner.get(worker_id, 0) + 1
        return original_claim(self, worker_id, lease_seconds, import_run_uuid)

    monkeypatch.setattr(ImportMessageProcessor, "claim_next", recording_claim)

    def call_endpoint() -> None:
        start.wait(timeout=3)
        results.append(
            ImportReconstructionWorkerService(settings).process_next_batch(
                import_run_uuid=run_uuid, limit=2
            )
        )

    first = threading.Thread(target=call_endpoint, daemon=True)
    second = threading.Thread(target=call_endpoint, daemon=True)
    first.start()
    second.start()
    start.wait(timeout=3)
    first.join(timeout=10)
    second.join(timeout=10)
    assert not first.is_alive() and not second.is_alive()
    assert len(results) == 2
    assert len(owners) == 2
    assert all(":api:" in str(owner) for owner in owners)
    assert all(count <= 2 for count in claims_by_owner.values())
    with import_connection(settings) as connection:
        duplicates = connection.execute(
            """
            SELECT processing_identity_hash, COUNT(*) AS copies
            FROM memory_processing_runs
            GROUP BY processing_identity_hash
            HAVING COUNT(*) > 1
            """
        ).fetchall()
    assert duplicates == []


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


def test_idle_worker_retries_terminal_finalization_after_transient_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path, concurrency=1)
    run_uuid = _commit(settings, tmp_path, monkeypatch, messages=1)
    original_finalize = ImportMessageProcessor.finalize_if_terminal
    injected = {"raised": False}

    def fail_first_terminal_finalize(self: ImportMessageProcessor, target_run_uuid: str) -> bool:
        report = self.processing_report(target_run_uuid)
        if report["terminal"] and not injected["raised"]:
            injected["raised"] = True
            raise sqlite3.OperationalError("database is locked")
        return original_finalize(self, target_run_uuid)

    monkeypatch.setattr(
        ImportMessageProcessor, "finalize_if_terminal", fail_first_terminal_finalize
    )
    worker = ImportReconstructionWorkerService(settings)
    worker.start()
    try:
        final = _wait_terminal(settings, run_uuid, timeout=8.0)
    finally:
        worker.stop()
    assert injected["raised"] is True
    assert final["status"] == "fully_reconstructed"


def test_slow_provider_heartbeat_prevents_a_second_worker_from_stealing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path, concurrency=1)
    run_uuid = _commit(settings, tmp_path, monkeypatch, messages=1)
    original = MemoryWorkerPipeline.prepare_message
    entered = threading.Event()

    def slow_prepare(
        self: MemoryWorkerPipeline,
        message_uuid: str,
        model_target: dict[str, object] | None = None,
    ) -> PreparedJakobsonInference:
        entered.set()
        time.sleep(2.2)
        return original(self, message_uuid, model_target)

    monkeypatch.setattr(MemoryWorkerPipeline, "prepare_message", slow_prepare)
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


def test_slow_import_inference_does_not_block_live_capture(
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
        prepared = original(self, message_uuid, model_target)
        entered.set()
        assert release.wait(timeout=8)
        return prepared

    monkeypatch.setattr(MemoryWorkerPipeline, "prepare_message", blocked_prepare)
    monkeypatch.setattr(routes_openwebui, "get_settings", lambda: settings)
    worker = ImportReconstructionWorkerService(settings)
    thread = threading.Thread(
        target=lambda: worker.process_one_claimed_item(
            worker_id="slow-import", import_run_uuid=run_uuid, heartbeat=False
        ),
        daemon=True,
    )
    thread.start()
    assert entered.wait(timeout=3)
    started = time.perf_counter()
    captured = routes_openwebui.capture_message(
        MessageCaptureRequest(
            openwebui_conversation_id=f"live-{time.time_ns()}",
            role="user",
            content="This live message must not wait for import inference.",
        )
    )
    elapsed = time.perf_counter() - started
    assert captured["message_uuid"]
    assert elapsed < 1.0
    assert thread.is_alive()
    release.set()
    thread.join(timeout=8)
    assert not thread.is_alive()


def test_publication_can_finish_after_remaining_lease_expires(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path, concurrency=1)
    run_uuid = _commit(settings, tmp_path, monkeypatch, messages=1)
    original = MemoryWorkerPipeline.process_message

    def slow_publication(
        self: MemoryWorkerPipeline,
        message_uuid: str,
        import_run_uuid: str | None = None,
        job_uuid: str | None = None,
        model_target: dict[str, object] | None = None,
        lease_fence: Callable[[], None] | None = None,
        prepared_inference: PreparedJakobsonInference | None = None,
    ) -> dict[str, object]:
        time.sleep(1.2)
        return original(
            self,
            message_uuid,
            import_run_uuid,
            job_uuid,
            model_target,
            lease_fence,
            prepared_inference,
        )

    monkeypatch.setattr(MemoryWorkerPipeline, "process_message", slow_publication)
    with import_connection(settings) as connection:
        processor = ImportMessageProcessor(connection, settings)
        claimed = processor.claim_next(
            "slow-publication",
            settings.import_reconstruction_lease_seconds,
            run_uuid,
        )
        assert claimed is not None
        near_expiry = (
            (datetime.now(UTC).replace(microsecond=0) + timedelta(seconds=1))
            .isoformat()
            .replace("+00:00", "Z")
        )
        with connection:
            connection.execute(
                """
                UPDATE import_message_processing_status
                SET lease_expires_at = ?
                WHERE status_uuid = ?
                """,
                (near_expiry, claimed["status_uuid"]),
            )
        processor._process_one(claimed)
        status = connection.execute(
            """
            SELECT status, processing_stage, lease_owner, lease_expires_at
            FROM import_message_processing_status
            WHERE status_uuid = ?
            """,
            (claimed["status_uuid"],),
        ).fetchone()
    assert dict(status) == {
        "status": "succeeded",
        "processing_stage": "complete",
        "lease_owner": None,
        "lease_expires_at": None,
    }


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


def test_reclaimed_attempt_cannot_be_renewed_or_released_by_reused_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path, concurrency=1)
    run_uuid = _commit(settings, tmp_path, monkeypatch, messages=1)
    with import_connection(settings) as connection:
        processor = ImportMessageProcessor(connection, settings)
        first = processor.claim_next("same-owner", 1, run_uuid)
        assert first is not None
        connection.execute(
            """
            UPDATE import_message_processing_status
            SET lease_expires_at = '2000-01-01T00:00:00'
            WHERE status_uuid = ?
            """,
            (first["status_uuid"],),
        )
        connection.commit()
        assert not processor.renew_lease(
            str(first["status_uuid"]),
            "same-owner",
            str(first["processing_attempt_uuid"]),
            30,
        )
        assert processor.recover_expired_leases() == 1
        second = processor.claim_next("same-owner", 30, run_uuid)
        assert second is not None
        assert second["processing_attempt_uuid"] != first["processing_attempt_uuid"]
        assert not processor.renew_lease(
            str(first["status_uuid"]),
            "same-owner",
            str(first["processing_attempt_uuid"]),
            30,
        )
        assert not processor.release_claim(
            str(first["status_uuid"]),
            "same-owner",
            str(first["processing_attempt_uuid"]),
        )
        current = connection.execute(
            """
            SELECT status, lease_owner, processing_attempt_uuid
            FROM import_message_processing_status
            WHERE status_uuid = ?
            """,
            (second["status_uuid"],),
        ).fetchone()
    assert current["status"] == "running"
    assert current["lease_owner"] == "same-owner"
    assert current["processing_attempt_uuid"] == second["processing_attempt_uuid"]


def test_retryable_failure_waits_until_due_and_preserves_attempt_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path, concurrency=1)
    run_uuid = _commit(settings, tmp_path, monkeypatch, messages=1)
    original = MemoryWorkerPipeline.prepare_message
    calls = {"count": 0}

    def timeout_once(
        self: MemoryWorkerPipeline,
        message_uuid: str,
        model_target: dict[str, object] | None = None,
    ) -> PreparedJakobsonInference:
        calls["count"] += 1
        if calls["count"] == 1:
            raise TimeoutError("synthetic provider timeout")
        return original(self, message_uuid, model_target)

    monkeypatch.setattr(MemoryWorkerPipeline, "prepare_message", timeout_once)
    worker = ImportReconstructionWorkerService(settings)
    assert worker.process_one_claimed_item(worker_id="retry-worker", import_run_uuid=run_uuid)
    with import_connection(settings) as connection:
        status = connection.execute(
            """
            SELECT * FROM import_message_processing_status
            WHERE import_run_uuid = ? AND status = 'queued' AND run_after IS NOT NULL
            """,
            (run_uuid,),
        ).fetchone()
        assert status is not None
        job = connection.execute(
            "SELECT * FROM jobs WHERE job_uuid = ?", (status["job_uuid"],)
        ).fetchone()
        assert status["retry_count"] == 1
        assert status["error_classification"] == "retryable_network"
        assert job["status"] == "pending"
        assert job["attempts"] == 1
        assert (
            ImportMessageProcessor(connection, settings).claim_next("too-early", 30, run_uuid)
            is None
        )
        connection.execute(
            """
            UPDATE import_message_processing_status
            SET run_after = '2000-01-01T00:00:00'
            WHERE status_uuid = ?
            """,
            (status["status_uuid"],),
        )
        connection.commit()
    assert worker.process_one_claimed_item(worker_id="retry-worker", import_run_uuid=run_uuid)
    with import_connection(settings) as connection:
        status = connection.execute(
            "SELECT * FROM import_message_processing_status WHERE status_uuid = ?",
            (status["status_uuid"],),
        ).fetchone()
        job = connection.execute(
            "SELECT * FROM jobs WHERE job_uuid = ?", (status["job_uuid"],)
        ).fetchone()
    assert status["status"] == "succeeded"
    assert status["retry_count"] == 1
    assert status["run_after"] is None
    assert job["status"] == "succeeded"
    assert job["attempts"] == 2


def test_midflight_lost_lease_discards_late_attempt_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path, concurrency=1)
    run_uuid = _commit(
        settings,
        tmp_path,
        monkeypatch,
        messages=1,
        first_message_text="Remember this: I prefer tea.",
    )
    original = MemoryWorkerPipeline.prepare_message
    entered = threading.Event()
    release_late_attempt = threading.Event()
    call_lock = threading.Lock()
    calls = {"count": 0}

    def prepare_then_block(
        self: MemoryWorkerPipeline,
        message_uuid: str,
        model_target: dict[str, object] | None = None,
    ) -> PreparedJakobsonInference:
        prepared = original(self, message_uuid, model_target)
        with call_lock:
            calls["count"] += 1
            call_number = calls["count"]
        marked_output = dict(prepared.output)
        marked_output["warnings"] = [f"attempt-{'a' if call_number == 1 else 'b'}"]
        prepared = replace(prepared, output=marked_output)
        if call_number == 1:
            entered.set()
            assert release_late_attempt.wait(timeout=8)
        return prepared

    monkeypatch.setattr(MemoryWorkerPipeline, "prepare_message", prepare_then_block)
    with import_connection(settings) as connection:
        claimed_a = ImportMessageProcessor(connection, settings).claim_next("worker-a", 1, run_uuid)
    assert claimed_a is not None

    def run_late_attempt() -> None:
        with import_connection(settings) as connection:
            ImportMessageProcessor(connection, settings)._process_one(dict(claimed_a))

    late_thread = threading.Thread(target=run_late_attempt, daemon=True)
    late_thread.start()
    assert entered.wait(timeout=3)

    with import_connection(settings) as connection:
        connection.execute(
            """
            UPDATE import_message_processing_status
            SET lease_expires_at = '2000-01-01T00:00:00'
            WHERE status_uuid = ?
            """,
            (claimed_a["status_uuid"],),
        )
        connection.commit()
        assert ImportMessageProcessor(connection, settings).recover_expired_leases() == 1

    worker_b = ImportReconstructionWorkerService(settings)
    assert worker_b.process_one_claimed_item(worker_id="worker-b", import_run_uuid=run_uuid)
    release_late_attempt.set()
    late_thread.join(timeout=8)
    assert not late_thread.is_alive()

    with import_connection(settings) as connection:
        status = dict(
            connection.execute(
                """
                SELECT status, lease_owner, processing_attempt_uuid
                FROM import_message_processing_status
                WHERE status_uuid = ?
                """,
                (claimed_a["status_uuid"],),
            ).fetchone()
        )
        tables = {
            "runs": [
                dict(row) for row in connection.execute("SELECT * FROM memory_processing_runs")
            ],
            "prompts": [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM prompt_execution_runs WHERE import_run_uuid = ?",
                    (run_uuid,),
                )
            ],
            "usage": [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM model_usage_events WHERE import_run_uuid = ?",
                    (run_uuid,),
                )
            ],
            "jakobson": [
                dict(row) for row in connection.execute("SELECT * FROM jakobson_analysis_runs")
            ],
            "candidates": [
                dict(row) for row in connection.execute("SELECT * FROM memory_candidates")
            ],
            "versions": [dict(row) for row in connection.execute("SELECT * FROM memory_versions")],
            "outbox": [
                dict(row) for row in connection.execute("SELECT * FROM graph_projection_outbox")
            ],
        }
    rendered = repr(tables)
    assert status["status"] == "succeeded"
    assert status["lease_owner"] is None
    assert status["processing_attempt_uuid"] != claimed_a["processing_attempt_uuid"]
    assert len(tables["runs"]) == 1
    assert len(tables["prompts"]) == 1
    jakobson_usage = [
        row for row in tables["usage"] if row["stage"] == "jakobson_sentence_analysis"
    ]
    assert len(jakobson_usage) == 1
    assert len(tables["jakobson"]) == 1
    assert tables["candidates"]
    assert tables["versions"]
    assert tables["outbox"]
    assert "attempt-a" not in rendered
    assert "attempt-b" in rendered


def test_restart_recovery_finishes_remaining_queue_without_duplicates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path, concurrency=1)
    run_uuid = _commit(settings, tmp_path, monkeypatch, messages=4)
    original = MemoryWorkerPipeline.prepare_message
    entered = threading.Event()

    def first_slow(
        self: MemoryWorkerPipeline,
        message_uuid: str,
        model_target: dict[str, object] | None = None,
    ) -> PreparedJakobsonInference:
        entered.set()
        time.sleep(0.3)
        return original(self, message_uuid, model_target)

    monkeypatch.setattr(MemoryWorkerPipeline, "prepare_message", first_slow)
    first = ImportReconstructionWorkerService(settings)
    first.start()
    assert entered.wait(timeout=3)
    first.stop()
    monkeypatch.setattr(MemoryWorkerPipeline, "prepare_message", original)

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


def test_cancel_during_inference_discards_late_result_and_preserves_prior_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path, concurrency=1)
    run_uuid = _commit(
        settings,
        tmp_path,
        monkeypatch,
        messages=2,
        first_message_text="Remember this: I prefer tea.",
    )
    worker = ImportReconstructionWorkerService(settings)
    assert worker.process_one_claimed_item(worker_id="first-worker", import_run_uuid=run_uuid)
    with import_connection(settings) as connection:
        before = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "memory_processing_runs",
                "prompt_execution_runs",
                "model_usage_events",
                "jakobson_analysis_runs",
                "memory_candidates",
                "memory_versions",
                "graph_projection_outbox",
            )
        }

    original = MemoryWorkerPipeline.prepare_message
    entered = threading.Event()
    release = threading.Event()

    def blocked_prepare(
        self: MemoryWorkerPipeline,
        message_uuid: str,
        model_target: dict[str, object] | None = None,
    ) -> PreparedJakobsonInference:
        prepared = original(self, message_uuid, model_target)
        entered.set()
        assert release.wait(timeout=8)
        return prepared

    monkeypatch.setattr(MemoryWorkerPipeline, "prepare_message", blocked_prepare)
    late = threading.Thread(
        target=lambda: worker.process_one_claimed_item(
            worker_id="cancelled-worker", import_run_uuid=run_uuid, heartbeat=False
        ),
        daemon=True,
    )
    late.start()
    assert entered.wait(timeout=3)
    with import_connection(settings) as connection:
        ImportService(connection, settings.object_store_path).cancel(run_uuid)
    release.set()
    late.join(timeout=8)
    assert not late.is_alive()

    with import_connection(settings) as connection:
        statuses = [
            dict(row)
            for row in connection.execute(
                """
                SELECT status, lease_owner FROM import_message_processing_status
                WHERE import_run_uuid = ? ORDER BY created_at, status_uuid
                """,
                (run_uuid,),
            )
        ]
        after = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in before
        }
    processable_statuses = {item["status"] for item in statuses if item["status"] != "skipped"}
    assert processable_statuses == {"succeeded", "cancelled"}
    assert all(item["lease_owner"] is None for item in statuses)
    assert after == before


def test_processing_report_uses_latest_error_by_updated_at(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path, concurrency=1)
    run_uuid = _commit(settings, tmp_path, monkeypatch, messages=2)
    with import_connection(settings) as connection:
        statuses = connection.execute(
            """
            SELECT status_uuid FROM import_message_processing_status
            WHERE import_run_uuid = ?
            ORDER BY created_at, status_uuid
            """,
            (run_uuid,),
        ).fetchall()
        assert len(statuses) >= 2
        earlier = statuses[0]["status_uuid"]
        later = statuses[1]["status_uuid"]
        with connection:
            connection.execute(
                """
                UPDATE import_message_processing_status
                SET status = 'failed', error_sanitized = 'later-created failed first',
                    updated_at = '2026-01-01T00:00:00'
                WHERE status_uuid = ?
                """,
                (later,),
            )
            connection.execute(
                """
                UPDATE import_message_processing_status
                SET status = 'failed', error_sanitized = 'earlier-created failed afterward',
                    updated_at = '2026-01-01T00:00:01'
                WHERE status_uuid = ?
                """,
                (earlier,),
            )
        report = ImportMessageProcessor(connection, settings).processing_report(run_uuid)
    assert report["last_error_sanitized"] == "earlier-created failed afterward"


def test_processing_report_does_not_infer_prompt_role_from_unrelated_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path, concurrency=1)
    run_uuid = _commit(settings, tmp_path, monkeypatch, messages=1)
    worker = ImportReconstructionWorkerService(settings)
    assert worker.process_one_claimed_item(
        worker_id="report-provenance-worker", import_run_uuid=run_uuid
    )
    with import_connection(settings) as connection:
        unrelated = JobRepository(connection).enqueue_job_once(
            "unrelated_prompt_audit",
            {"import_run_uuid": run_uuid, "purpose": "report-contract"},
        )
        PromptExecutionRepository(connection).record_execution(
            prompt_id=JAKOBSON_SENTENCE_ANALYSIS_PROMPT_ID,
            prompt_version=PROMPT_PACK_VERSION,
            model_role=ModelRole.MEMORY_EXTRACTION,
            provider_type="deterministic",
            model_name="deterministic_extraction",
            input_value={"sentences": []},
            raw_output_value={"sentences": []},
            import_run_uuid=run_uuid,
            job_uuid=unrelated.job_uuid,
        )
        report = ImportMessageProcessor(connection, settings).processing_report(run_uuid)
    import_rows = [
        row
        for row in report["provider_breakdown"]
        if row["operational_role"] == "import_reconstruction"
        and row["provider_type"] == "deterministic"
        and row["model_name"] == "deterministic_extraction"
        and row["processing_jobs"] > 0
    ]
    assert len(import_rows) == 1
    assert "prompt_role" not in import_rows[0]
