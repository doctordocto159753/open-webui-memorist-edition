from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import replace
from pathlib import Path
from typing import Any
from uuid import uuid4
from zipfile import ZipFile

import pytest

from memcore.api import routes_openwebui
from memcore.api.routes_openwebui import MessageCaptureRequest
from memcore.config import Settings
from memcore.imports.processing import ImportMessageProcessor
from memcore.imports.runtime import import_connection, initialize_runtime_storage
from memcore.imports.service import ImportService
from memcore.imports.worker import ImportReconstructionWorkerService
from memcore.memory_worker.postgres.pipeline import PostgresMemoryWorkerPipeline
from memcore.memory_worker.prepared import PreparedJakobsonInference


def _pg_settings(tmp_path: Path) -> Settings:
    return Settings(
        runtime_profile="full",
        canonical_store="postgres",
        postgres_dsn=os.environ["MEMORIST_POSTGRES_DSN"],
        object_store_path=str(tmp_path / "objects"),
        db_path=str(tmp_path / "lite.sqlite3"),
        allow_full_graph_degraded=True,
        hot_scheduler="in_memory",
        import_reconstruction_worker_enabled=True,
        import_reconstruction_poll_seconds=1,
        import_reconstruction_lease_seconds=3,
        import_reconstruction_heartbeat_seconds=1,
    )


def _pg_committed_run(
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
            str(
                _pg_archive(
                    tmp_path,
                    messages=messages,
                    first_message_text=first_message_text,
                )
            )
        )
        run_uuid = str(run["import_run_uuid"])
        service.inspect(run_uuid)
        service.reconstruct(run_uuid)
        service.dry_run(run_uuid, "full_memory_reconstruction")
        service.commit(run_uuid, "full_memory_reconstruction")
        return run_uuid


def _pg_archive(path: Path, messages: int = 4, first_message_text: str | None = None) -> Path:
    unique = uuid4().hex
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
                "id": f"{unique}-{node_id}",
                "author": {"role": "user" if index % 2 == 0 else "assistant"},
                "create_time": 1_700_000_000 + index,
                "content": {
                    "content_type": "text",
                    "parts": [
                        first_message_text
                        if index == 0 and first_message_text is not None
                        else f"Durable PostgreSQL fact {unique} {index}."
                    ],
                },
            },
        }
    archive = path / f"chatgpt-{unique}.zip"
    with ZipFile(archive, "w") as zip_file:
        zip_file.writestr(
            "conversations.json",
            json.dumps(
                [
                    {
                        "id": f"worker-lifecycle-{unique}",
                        "title": "Worker lifecycle",
                        "create_time": 1_700_000_000,
                        "mapping": mapping,
                    }
                ]
            ),
        )
    return archive


@pytest.mark.skipif(not os.getenv("MEMORIST_POSTGRES_DSN"), reason="requires real PostgreSQL")
def test_full_postgres_import_runtime_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MEMORIST_RUNTIME_PROFILE", "full")
    monkeypatch.setenv("MEMORIST_CANONICAL_STORE", "postgres")
    settings = Settings(
        runtime_profile="full",
        canonical_store="postgres",
        postgres_dsn=os.environ["MEMORIST_POSTGRES_DSN"],
        object_store_path=str(tmp_path / "objects"),
        db_path=str(tmp_path / "lite.sqlite3"),
        allow_full_graph_degraded=True,
        hot_scheduler="in_memory",
    )
    archive = tmp_path / "chatgpt.zip"
    export = [
        {
            "id": "conv-full-pg",
            "title": "Full PostgreSQL import",
            "create_time": 1700000000,
            "mapping": {
                "root": {"id": "root", "message": None, "parent": None, "children": ["u"]},
                "u": {
                    "id": "u",
                    "parent": "root",
                    "children": ["a"],
                    "message": {
                        "id": "u",
                        "author": {"role": "user"},
                        "create_time": 1700000001,
                        "content": {
                            "content_type": "text",
                            "parts": ["Remember that Ada likes graph databases."],
                        },
                    },
                },
                "a": {
                    "id": "a",
                    "parent": "u",
                    "children": [],
                    "message": {
                        "id": "a",
                        "author": {"role": "assistant"},
                        "create_time": 1700000002,
                        "content": {
                            "content_type": "text",
                            "parts": ["Got it: Ada likes graph databases."],
                        },
                    },
                },
            },
        }
    ]
    with ZipFile(archive, "w") as zf:
        zf.writestr("conversations.json", json.dumps(export))

    initialize_runtime_storage(settings)

    with import_connection(settings) as connection:
        service = ImportService(connection, settings.object_store_path)
        run = service.upload(
            str(archive),
            mode="inspect",
            options={"processing_mode": "full_memory_reconstruction"},
        )
        run = service.inspect(run["import_run_uuid"])
        assert run["source_platform"] == "chatgpt"
        service.reconstruct(run["import_run_uuid"])
        service.dry_run(run["import_run_uuid"], "full_memory_reconstruction")
        committed = service.commit(run["import_run_uuid"], "full_memory_reconstruction")
        assert committed["status"] in {"processing", "fully_reconstructed"}
    worker = ImportReconstructionWorkerService(settings)
    worker.start()
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            with import_connection(settings) as polling_connection:
                current = ImportService(
                    polling_connection, settings.object_store_path
                ).repository.get_run(run["import_run_uuid"])
                if current["status"] in {"fully_reconstructed", "completed_with_failures"}:
                    break
            time.sleep(0.05)
        else:
            raise AssertionError("PostgreSQL background worker did not complete before timeout")
    finally:
        worker.stop()

    with import_connection(settings) as connection:
        service = ImportService(connection, settings.object_store_path)
        final = service.repository.get_run(run["import_run_uuid"])
        assert final["status"] == "fully_reconstructed"
        counts = connection.execute(
            """
            SELECT
              (SELECT COUNT(*) FROM sessions) AS sessions,
              (SELECT COUNT(*) FROM messages) AS messages,
              (SELECT COUNT(*) FROM import_mappings WHERE import_run_uuid = ?) AS mappings,
              (SELECT COUNT(*) FROM import_message_processing_status
               WHERE import_run_uuid = ?) AS statuses,
              (SELECT COUNT(*) FROM import_message_processing_status
               WHERE import_run_uuid = ?
                 AND status IN ('succeeded', 'already_processed')) AS succeeded,
              (SELECT COUNT(*) FROM import_message_processing_status
               WHERE import_run_uuid = ? AND status = 'skipped') AS skipped,
              (SELECT COUNT(*) FROM memory_processing_runs) AS runs,
              (SELECT COUNT(*) FROM text_units) AS text_units,
              (SELECT COUNT(*) FROM memory_candidates) AS candidates,
              (SELECT COUNT(*) FROM prompt_execution_runs WHERE import_run_uuid = ?) AS prompts,
              (SELECT COUNT(*) FROM model_usage_events WHERE import_run_uuid = ?) AS usage_events,
              (SELECT COUNT(*) FROM graph_projection_outbox) AS outbox
            """,
            (
                run["import_run_uuid"],
                run["import_run_uuid"],
                run["import_run_uuid"],
                run["import_run_uuid"],
                run["import_run_uuid"],
                run["import_run_uuid"],
            ),
        ).fetchone()
        assert counts["sessions"] >= 1
        assert counts["messages"] >= 2
        assert counts["mappings"] >= 3
        assert counts["statuses"] >= 2
        assert counts["succeeded"] + counts["skipped"] == counts["statuses"]
        assert counts["runs"] >= 1
        assert counts["text_units"] >= 1
        assert counts["candidates"] >= 1
        assert counts["prompts"] >= 1
        assert counts["usage_events"] >= 1
        assert counts["outbox"] >= 1

    with import_connection(settings) as restarted:
        persisted = ImportService(restarted, settings.object_store_path).repository.get_run(
            run["import_run_uuid"]
        )
        assert persisted["import_run_uuid"] == run["import_run_uuid"]


@pytest.mark.skipif(not os.getenv("MEMORIST_POSTGRES_DSN"), reason="requires real PostgreSQL")
def test_full_postgres_two_workers_claim_distinct_messages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _pg_settings(tmp_path)
    run_uuid = _pg_committed_run(settings, tmp_path, monkeypatch, messages=4)
    with import_connection(settings) as connection:
        processor = ImportMessageProcessor(connection, settings)
        first = processor.claim_next("pg-worker-a", 10, run_uuid)
        second = processor.claim_next("pg-worker-b", 10, run_uuid)
        assert first is not None
        assert second is not None
        assert first["status_uuid"] != second["status_uuid"]
        assert first["target_message_uuid"] != second["target_message_uuid"]
        assert first["lease_owner"] == "pg-worker-a"
        assert second["lease_owner"] == "pg-worker-b"
        assert first["processing_attempt_uuid"]
        assert second["processing_attempt_uuid"]
        assert first["processing_attempt_uuid"] != second["processing_attempt_uuid"]
        ImportService(connection, settings.object_store_path).cancel(run_uuid)


@pytest.mark.skipif(not os.getenv("MEMORIST_POSTGRES_DSN"), reason="requires real PostgreSQL")
def test_full_postgres_expired_lease_reclaim_gets_new_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _pg_settings(tmp_path)
    run_uuid = _pg_committed_run(settings, tmp_path, monkeypatch, messages=1)
    with import_connection(settings) as connection:
        processor = ImportMessageProcessor(connection, settings)
        first = processor.claim_next("pg-stale-worker", 1, run_uuid)
        assert first is not None
        connection.execute(
            """
            UPDATE import_message_processing_status
            SET lease_expires_at = now() - interval '1 second'
            WHERE status_uuid = ?
            """,
            (first["status_uuid"],),
        )
        connection.commit()
        assert processor.recover_expired_leases() == 1
        reclaimed = processor.claim_next("pg-fresh-worker", 10, run_uuid)
        assert reclaimed is not None
        assert reclaimed["status_uuid"] == first["status_uuid"]
        assert reclaimed["lease_owner"] == "pg-fresh-worker"
        assert reclaimed["processing_attempt_uuid"] != first["processing_attempt_uuid"]
        ImportService(connection, settings.object_store_path).cancel(run_uuid)


@pytest.mark.skipif(not os.getenv("MEMORIST_POSTGRES_DSN"), reason="requires real PostgreSQL")
def test_full_postgres_concurrent_process_endpoint_does_not_duplicate_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _pg_settings(tmp_path)
    run_uuid = _pg_committed_run(settings, tmp_path, monkeypatch, messages=4)
    results: list[dict[str, object]] = []
    owners: list[str] = []
    calls_by_owner: dict[str, int] = {}
    owner_lock = threading.Lock()
    barrier = threading.Barrier(2)
    original_claim = ImportMessageProcessor.claim_next

    def recorded_claim(
        self: ImportMessageProcessor,
        worker_id: str,
        lease_seconds: int,
        import_run_uuid: str | None = None,
    ) -> dict[str, Any] | None:
        with owner_lock:
            if worker_id not in owners:
                owners.append(worker_id)
            calls_by_owner[worker_id] = calls_by_owner.get(worker_id, 0) + 1
            first_call = calls_by_owner[worker_id] == 1
        if first_call:
            barrier.wait(timeout=10)
        return original_claim(self, worker_id, lease_seconds, import_run_uuid)

    monkeypatch.setattr(ImportMessageProcessor, "claim_next", recorded_claim)

    def process_one_request() -> None:
        results.append(
            ImportReconstructionWorkerService(settings).process_next_batch(
                import_run_uuid=run_uuid,
                limit=2,
            )
        )

    first = threading.Thread(target=process_one_request, daemon=True)
    second = threading.Thread(target=process_one_request, daemon=True)
    first.start()
    second.start()
    first.join(timeout=30)
    second.join(timeout=30)
    assert not first.is_alive()
    assert not second.is_alive()
    assert len(results) == 2
    with import_connection(settings) as connection:
        duplicates = connection.execute(
            """
            SELECT processing_identity_hash, COUNT(*) AS copies
            FROM memory_processing_runs
            WHERE processing_identity_hash IS NOT NULL
            GROUP BY processing_identity_hash
            HAVING COUNT(*) > 1
            """
        ).fetchall()
        statuses = connection.execute(
            """
            SELECT COUNT(*) AS total,
                   COUNT(*) FILTER (
                     WHERE status IN ('succeeded', 'already_processed')
                   ) AS done
            FROM import_message_processing_status
            WHERE import_run_uuid = ?
              AND processing_mode = 'full_memory_reconstruction'
              AND processing_identity_hash IS NOT NULL
              AND job_uuid IS NOT NULL
            """,
            (run_uuid,),
        ).fetchone()
    assert len(owners) == 2
    assert len(set(owners)) == 2
    assert all(":api:" in owner for owner in owners)
    assert all(calls <= 2 for calls in calls_by_owner.values())
    assert duplicates == []
    assert int(statuses["total"]) == 4
    assert int(statuses["done"]) == 4


@pytest.mark.skipif(not os.getenv("MEMORIST_POSTGRES_DSN"), reason="requires real PostgreSQL")
def test_full_postgres_midflight_lease_loss_fences_all_attempt_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _pg_settings(tmp_path)
    run_uuid = _pg_committed_run(
        settings,
        tmp_path,
        monkeypatch,
        messages=1,
        first_message_text="Remember this: I prefer PostgreSQL.",
    )
    original = PostgresMemoryWorkerPipeline.prepare_message
    entered = threading.Event()
    release_late = threading.Event()
    call_lock = threading.Lock()
    calls = {"count": 0}

    def prepare_then_block(
        self: PostgresMemoryWorkerPipeline,
        message_uuid: str,
        model_target: dict[str, object] | None = None,
    ) -> PreparedJakobsonInference:
        prepared = original(self, message_uuid, model_target)
        with call_lock:
            calls["count"] += 1
            call_number = calls["count"]
        output = dict(prepared.output)
        output["warnings"] = [f"pg-attempt-{'a' if call_number == 1 else 'b'}"]
        prepared = replace(prepared, output=output)
        if call_number == 1:
            entered.set()
            assert release_late.wait(timeout=15)
        return prepared

    monkeypatch.setattr(PostgresMemoryWorkerPipeline, "prepare_message", prepare_then_block)
    with import_connection(settings) as connection:
        claimed_a = ImportMessageProcessor(connection, settings).claim_next(
            "pg-worker-a", 1, run_uuid
        )
    assert claimed_a is not None

    def late_attempt() -> None:
        with import_connection(settings) as connection:
            ImportMessageProcessor(connection, settings)._process_one(dict(claimed_a))

    thread = threading.Thread(target=late_attempt, daemon=True)
    thread.start()
    assert entered.wait(timeout=5)
    with import_connection(settings) as connection:
        connection.execute(
            """
            UPDATE import_message_processing_status
            SET lease_expires_at = now() - interval '1 second'
            WHERE status_uuid = ?
            """,
            (claimed_a["status_uuid"],),
        )
        connection.commit()
        job_before = connection.execute(
            "SELECT status, locked_by FROM jobs WHERE job_uuid = ?",
            (claimed_a["job_uuid"],),
        ).fetchone()
        assert dict(job_before) == {"status": "running", "locked_by": "pg-worker-a"}
        assert ImportMessageProcessor(connection, settings).recover_expired_leases() == 1
        job_after = connection.execute(
            "SELECT status, locked_by, locked_at FROM jobs WHERE job_uuid = ?",
            (claimed_a["job_uuid"],),
        ).fetchone()
        assert dict(job_after) == {
            "status": "pending",
            "locked_by": None,
            "locked_at": None,
        }
    assert ImportReconstructionWorkerService(settings).process_one_claimed_item(
        worker_id="pg-worker-b", import_run_uuid=run_uuid
    )
    release_late.set()
    thread.join(timeout=15)
    assert not thread.is_alive()

    message_uuid = str(claimed_a["target_message_uuid"])
    with import_connection(settings) as connection:
        status = connection.execute(
            """
            SELECT status, lease_owner, processing_attempt_uuid
            FROM import_message_processing_status
            WHERE status_uuid = ?
            """,
            (claimed_a["status_uuid"],),
        ).fetchone()
        tables = {
            "runs": [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM memory_processing_runs WHERE message_uuid = ?",
                    (message_uuid,),
                ).fetchall()
            ],
            "prompts": [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT * FROM prompt_execution_runs
                    WHERE import_run_uuid = ? AND message_uuid = ?
                    """,
                    (run_uuid, message_uuid),
                ).fetchall()
            ],
            "usage": [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT * FROM model_usage_events
                    WHERE import_run_uuid = ? AND message_uuid = ?
                    """,
                    (run_uuid, message_uuid),
                ).fetchall()
            ],
            "jakobson": [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM jakobson_analysis_runs WHERE message_uuid = ?",
                    (message_uuid,),
                ).fetchall()
            ],
            "candidates": [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT candidate.*
                    FROM memory_candidates candidate
                    JOIN text_units unit
                      ON unit.text_unit_uuid = candidate.text_unit_uuid
                    WHERE unit.message_uuid = ?
                    """,
                    (message_uuid,),
                ).fetchall()
            ],
            "versions": [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT version.*
                    FROM memory_versions version
                    JOIN memory_candidates candidate
                      ON candidate.candidate_uuid = version.source_candidate_uuid
                    JOIN text_units unit
                      ON unit.text_unit_uuid = candidate.text_unit_uuid
                    WHERE unit.message_uuid = ?
                    """,
                    (message_uuid,),
                ).fetchall()
            ],
            "outbox": [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT outbox.*
                    FROM graph_projection_outbox outbox
                    WHERE outbox.source_uuid IN (
                        SELECT analysis_run_uuid
                        FROM jakobson_analysis_runs
                        WHERE message_uuid = ?
                        UNION
                        SELECT version.memory_uuid
                        FROM memory_versions version
                        JOIN memory_candidates candidate
                          ON candidate.candidate_uuid = version.source_candidate_uuid
                        JOIN text_units unit
                          ON unit.text_unit_uuid = candidate.text_unit_uuid
                        WHERE unit.message_uuid = ?
                    )
                    """,
                    (message_uuid, message_uuid),
                ).fetchall()
            ],
        }
    rendered = repr(tables)
    assert status["status"] == "succeeded"
    assert status["lease_owner"] is None
    assert status["processing_attempt_uuid"] != claimed_a["processing_attempt_uuid"]
    assert len(tables["runs"]) == 1
    assert len(tables["prompts"]) == 1
    assert (
        len([row for row in tables["usage"] if row["stage"] == "jakobson_sentence_analysis"]) == 1
    )
    assert len(tables["jakobson"]) == 1
    assert tables["candidates"]
    assert tables["versions"]
    assert tables["outbox"]
    assert "pg-attempt-a" not in rendered
    assert "pg-attempt-b" in rendered


@pytest.mark.skipif(not os.getenv("MEMORIST_POSTGRES_DSN"), reason="requires real PostgreSQL")
def test_full_postgres_slow_inference_does_not_block_live_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _pg_settings(tmp_path)
    run_uuid = _pg_committed_run(settings, tmp_path, monkeypatch, messages=1)
    original = PostgresMemoryWorkerPipeline.prepare_message
    entered = threading.Event()
    release = threading.Event()

    def blocked_prepare(
        self: PostgresMemoryWorkerPipeline,
        message_uuid: str,
        model_target: dict[str, object] | None = None,
    ) -> PreparedJakobsonInference:
        prepared = original(self, message_uuid, model_target)
        entered.set()
        assert release.wait(timeout=15)
        return prepared

    monkeypatch.setattr(PostgresMemoryWorkerPipeline, "prepare_message", blocked_prepare)
    monkeypatch.setattr(routes_openwebui, "get_settings", lambda: settings)
    worker = ImportReconstructionWorkerService(settings)
    thread = threading.Thread(
        target=lambda: worker.process_one_claimed_item(
            worker_id="pg-slow-import", import_run_uuid=run_uuid, heartbeat=False
        ),
        daemon=True,
    )
    thread.start()
    assert entered.wait(timeout=5)
    started = time.perf_counter()
    captured = routes_openwebui.capture_message(
        MessageCaptureRequest(
            openwebui_conversation_id=f"pg-live-{uuid4().hex}",
            role="user",
            content="PostgreSQL live capture must not wait for import inference.",
        )
    )
    elapsed = time.perf_counter() - started
    assert captured["message_uuid"]
    assert elapsed < 1.0
    assert thread.is_alive()
    release.set()
    thread.join(timeout=15)
    assert not thread.is_alive()


@pytest.mark.skipif(not os.getenv("MEMORIST_POSTGRES_DSN"), reason="requires real PostgreSQL")
def test_full_postgres_reused_owner_cannot_control_new_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _pg_settings(tmp_path)
    run_uuid = _pg_committed_run(settings, tmp_path, monkeypatch, messages=1)
    with import_connection(settings) as connection:
        processor = ImportMessageProcessor(connection, settings)
        first = processor.claim_next("pg-same-owner", 1, run_uuid)
        assert first is not None
        connection.execute(
            """
            UPDATE import_message_processing_status
            SET lease_expires_at = now() - interval '1 second'
            WHERE status_uuid = ?
            """,
            (first["status_uuid"],),
        )
        connection.commit()
        assert not processor.renew_lease(
            str(first["status_uuid"]),
            "pg-same-owner",
            str(first["processing_attempt_uuid"]),
            30,
        )
        assert processor.recover_expired_leases() == 1
        second = processor.claim_next("pg-same-owner", 30, run_uuid)
        assert second is not None
        assert second["processing_attempt_uuid"] != first["processing_attempt_uuid"]
        assert not processor.renew_lease(
            str(first["status_uuid"]),
            "pg-same-owner",
            str(first["processing_attempt_uuid"]),
            30,
        )
        assert not processor.release_claim(
            str(first["status_uuid"]),
            "pg-same-owner",
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
        ImportService(connection, settings.object_store_path).cancel(run_uuid)
    assert current["status"] == "running"
    assert current["lease_owner"] == "pg-same-owner"
    assert current["processing_attempt_uuid"] == second["processing_attempt_uuid"]


@pytest.mark.skipif(not os.getenv("MEMORIST_POSTGRES_DSN"), reason="requires real PostgreSQL")
def test_full_postgres_slow_inference_heartbeat_prevents_reclaim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _pg_settings(tmp_path)
    run_uuid = _pg_committed_run(settings, tmp_path, monkeypatch, messages=1)
    original = PostgresMemoryWorkerPipeline.prepare_message
    entered = threading.Event()

    def slow_prepare(
        self: PostgresMemoryWorkerPipeline,
        message_uuid: str,
        model_target: dict[str, object] | None = None,
    ) -> PreparedJakobsonInference:
        entered.set()
        time.sleep(4.2)
        return original(self, message_uuid, model_target)

    monkeypatch.setattr(PostgresMemoryWorkerPipeline, "prepare_message", slow_prepare)
    worker = ImportReconstructionWorkerService(settings)
    thread = threading.Thread(
        target=lambda: worker.process_one_claimed_item(
            worker_id="pg-heartbeat-owner", import_run_uuid=run_uuid
        ),
        daemon=True,
    )
    thread.start()
    assert entered.wait(timeout=5)
    time.sleep(3.3)
    assert not worker.process_one_claimed_item(
        worker_id="pg-would-be-thief", import_run_uuid=run_uuid
    )
    thread.join(timeout=15)
    assert not thread.is_alive()


@pytest.mark.skipif(not os.getenv("MEMORIST_POSTGRES_DSN"), reason="requires real PostgreSQL")
def test_full_postgres_pause_has_bounded_polling_and_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _pg_settings(tmp_path)
    run_uuid = _pg_committed_run(settings, tmp_path, monkeypatch, messages=2)
    with import_connection(settings) as connection:
        ImportService(connection, settings.object_store_path).pause(run_uuid)
    original_claim = ImportMessageProcessor.claim_next
    claim_calls = {"count": 0}
    lock = threading.Lock()

    def counted_claim(
        self: ImportMessageProcessor,
        worker_id: str,
        lease_seconds: int,
        import_run_uuid: str | None = None,
    ) -> dict[str, Any] | None:
        with lock:
            claim_calls["count"] += 1
        return original_claim(self, worker_id, lease_seconds, import_run_uuid)

    monkeypatch.setattr(ImportMessageProcessor, "claim_next", counted_claim)
    worker = ImportReconstructionWorkerService(settings)
    worker.start()
    try:
        time.sleep(2.2)
        paused_calls = claim_calls["count"]
        with import_connection(settings) as connection:
            running = connection.execute(
                """
                SELECT COUNT(*) FROM import_message_processing_status
                WHERE import_run_uuid = ? AND status = 'running'
                """,
                (run_uuid,),
            ).fetchone()[0]
            assert running == 0
            ImportService(connection, settings.object_store_path).resume(run_uuid)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            with import_connection(settings) as connection:
                state = connection.execute(
                    "SELECT status FROM import_runs WHERE import_run_uuid = ?",
                    (run_uuid,),
                ).fetchone()["status"]
            if state == "fully_reconstructed":
                break
            time.sleep(0.05)
        else:
            raise AssertionError("PostgreSQL paused run did not resume")
    finally:
        worker.stop()
    assert paused_calls <= 6


@pytest.mark.skipif(not os.getenv("MEMORIST_POSTGRES_DSN"), reason="requires real PostgreSQL")
def test_full_postgres_pause_during_inference_requeues_claim_and_job_immediately(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _pg_settings(tmp_path)
    run_uuid = _pg_committed_run(settings, tmp_path, monkeypatch, messages=1)
    original = PostgresMemoryWorkerPipeline.prepare_message
    entered = threading.Event()
    release = threading.Event()

    def blocked_prepare(
        self: PostgresMemoryWorkerPipeline,
        message_uuid: str,
        model_target: dict[str, object] | None = None,
    ) -> PreparedJakobsonInference:
        entered.set()
        assert release.wait(timeout=15)
        return original(self, message_uuid, model_target)

    monkeypatch.setattr(PostgresMemoryWorkerPipeline, "prepare_message", blocked_prepare)
    worker = ImportReconstructionWorkerService(settings)
    late = threading.Thread(
        target=lambda: worker.process_one_claimed_item(
            worker_id="pg-paused-worker", import_run_uuid=run_uuid, heartbeat=False
        ),
        daemon=True,
    )
    late.start()
    assert entered.wait(timeout=5)
    with import_connection(settings) as connection:
        ImportService(connection, settings.object_store_path).pause(run_uuid)
    release.set()
    late.join(timeout=15)
    assert not late.is_alive()

    with import_connection(settings) as connection:
        status = connection.execute(
            """
            SELECT status, lease_owner, processing_attempt_uuid, job_uuid, target_message_uuid
            FROM import_message_processing_status
            WHERE import_run_uuid = ? AND skip_reason IS NULL
            """,
            (run_uuid,),
        ).fetchone()
        job = connection.execute(
            "SELECT status, locked_by, locked_at FROM jobs WHERE job_uuid = ?",
            (status["job_uuid"],),
        ).fetchone()
        message_uuid = str(status["target_message_uuid"])
        artifacts = connection.execute(
            """
            SELECT
              (SELECT COUNT(*) FROM memory_processing_runs WHERE message_uuid = ?) AS runs,
              (SELECT COUNT(*) FROM prompt_execution_runs
               WHERE import_run_uuid = ? AND message_uuid = ?) AS prompts,
              (SELECT COUNT(*) FROM model_usage_events
               WHERE import_run_uuid = ? AND message_uuid = ?
                 AND stage = 'jakobson_sentence_analysis') AS usage,
              (SELECT COUNT(*) FROM jakobson_analysis_runs WHERE message_uuid = ?) AS jakobson,
              (SELECT COUNT(*) FROM memory_candidates mc
               JOIN text_units tu ON tu.text_unit_uuid = mc.text_unit_uuid
               WHERE tu.message_uuid = ?) AS candidates,
              (SELECT COUNT(*) FROM memory_versions mv
               JOIN memory_candidates mc ON mc.candidate_uuid = mv.source_candidate_uuid
               JOIN text_units tu ON tu.text_unit_uuid = mc.text_unit_uuid
               WHERE tu.message_uuid = ?) AS versions
            """,
            (
                message_uuid,
                run_uuid,
                message_uuid,
                run_uuid,
                message_uuid,
                message_uuid,
                message_uuid,
                message_uuid,
            ),
        ).fetchone()
    assert status["status"] == "queued"
    assert status["lease_owner"] is None
    assert status["processing_attempt_uuid"] is None
    assert dict(job) == {"status": "pending", "locked_by": None, "locked_at": None}
    assert all(int(value) == 0 for value in artifacts.values())

    with import_connection(settings) as connection:
        ImportService(connection, settings.object_store_path).resume(run_uuid)
    monkeypatch.setattr(PostgresMemoryWorkerPipeline, "prepare_message", original)
    assert worker.process_one_claimed_item(worker_id="pg-resumed-worker", import_run_uuid=run_uuid)
    with import_connection(settings) as connection:
        final = connection.execute(
            "SELECT status FROM import_runs WHERE import_run_uuid = ?",
            (run_uuid,),
        ).fetchone()
    assert final["status"] == "fully_reconstructed"


@pytest.mark.skipif(not os.getenv("MEMORIST_POSTGRES_DSN"), reason="requires real PostgreSQL")
def test_full_postgres_cancel_during_inference_discards_late_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _pg_settings(tmp_path)
    run_uuid = _pg_committed_run(
        settings,
        tmp_path,
        monkeypatch,
        messages=2,
        first_message_text="Remember that PostgreSQL is preferred.",
    )
    worker = ImportReconstructionWorkerService(settings)
    assert worker.process_one_claimed_item(worker_id="pg-first-success", import_run_uuid=run_uuid)
    with import_connection(settings) as connection:
        before = connection.execute(
            """
            SELECT
              (SELECT COUNT(*) FROM memory_processing_runs WHERE status = 'succeeded') AS runs,
              (SELECT COUNT(*) FROM prompt_execution_runs WHERE import_run_uuid = ?) AS prompts,
              (SELECT COUNT(*) FROM model_usage_events WHERE import_run_uuid = ?) AS usage,
              (SELECT COUNT(*) FROM jakobson_analysis_runs) AS jakobson,
              (SELECT COUNT(*) FROM memory_candidates) AS candidates,
              (SELECT COUNT(*) FROM memory_versions) AS versions,
              (SELECT COUNT(*) FROM graph_projection_outbox) AS outbox
            """,
            (run_uuid, run_uuid),
        ).fetchone()

    original = PostgresMemoryWorkerPipeline.prepare_message
    entered = threading.Event()
    release = threading.Event()

    def blocked_prepare(
        self: PostgresMemoryWorkerPipeline,
        message_uuid: str,
        model_target: dict[str, object] | None = None,
    ) -> PreparedJakobsonInference:
        prepared = original(self, message_uuid, model_target)
        entered.set()
        assert release.wait(timeout=15)
        return prepared

    monkeypatch.setattr(PostgresMemoryWorkerPipeline, "prepare_message", blocked_prepare)
    late = threading.Thread(
        target=lambda: worker.process_one_claimed_item(
            worker_id="pg-cancelled-attempt",
            import_run_uuid=run_uuid,
            heartbeat=False,
        ),
        daemon=True,
    )
    late.start()
    assert entered.wait(timeout=5)
    with import_connection(settings) as connection:
        ImportService(connection, settings.object_store_path).cancel(run_uuid)
    release.set()
    late.join(timeout=15)
    assert not late.is_alive()

    with import_connection(settings) as connection:
        after = connection.execute(
            """
            SELECT
              (SELECT COUNT(*) FROM memory_processing_runs WHERE status = 'succeeded') AS runs,
              (SELECT COUNT(*) FROM prompt_execution_runs WHERE import_run_uuid = ?) AS prompts,
              (SELECT COUNT(*) FROM model_usage_events WHERE import_run_uuid = ?) AS usage,
              (SELECT COUNT(*) FROM jakobson_analysis_runs) AS jakobson,
              (SELECT COUNT(*) FROM memory_candidates) AS candidates,
              (SELECT COUNT(*) FROM memory_versions) AS versions,
              (SELECT COUNT(*) FROM graph_projection_outbox) AS outbox
            """,
            (run_uuid, run_uuid),
        ).fetchone()
        statuses = connection.execute(
            """
            SELECT status, lease_owner FROM import_message_processing_status
            WHERE import_run_uuid = ? ORDER BY created_at, status_uuid
            """,
            (run_uuid,),
        ).fetchall()
    assert dict(after) == dict(before)
    processable_statuses = {row["status"] for row in statuses if row["status"] != "skipped"}
    assert processable_statuses == {"succeeded", "cancelled"}
    assert all(row["lease_owner"] is None for row in statuses)


@pytest.mark.skipif(not os.getenv("MEMORIST_POSTGRES_DSN"), reason="requires real PostgreSQL")
def test_full_postgres_retry_schedule_is_due_gated_and_attempts_are_monotonic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _pg_settings(tmp_path)
    run_uuid = _pg_committed_run(settings, tmp_path, monkeypatch, messages=1)
    original = PostgresMemoryWorkerPipeline.prepare_message
    calls = {"count": 0}

    def timeout_once(
        self: PostgresMemoryWorkerPipeline,
        message_uuid: str,
        model_target: dict[str, object] | None = None,
    ) -> PreparedJakobsonInference:
        calls["count"] += 1
        if calls["count"] == 1:
            raise TimeoutError("provider inference timeout")
        return original(self, message_uuid, model_target)

    monkeypatch.setattr(PostgresMemoryWorkerPipeline, "prepare_message", timeout_once)
    worker = ImportReconstructionWorkerService(settings)
    assert worker.process_one_claimed_item(worker_id="pg-retry-a", import_run_uuid=run_uuid)
    with import_connection(settings) as connection:
        status = connection.execute(
            """
            SELECT status_uuid, status, retry_count, run_after, job_uuid
            FROM import_message_processing_status
            WHERE import_run_uuid = ? AND processing_stage = 'memory_extraction'
            """,
            (run_uuid,),
        ).fetchone()
        job = connection.execute(
            "SELECT status, attempts, run_after FROM jobs WHERE job_uuid = ?",
            (status["job_uuid"],),
        ).fetchone()
        report = ImportMessageProcessor(connection, settings).processing_report(run_uuid)
        assert (
            ImportMessageProcessor(connection, settings).claim_next("pg-too-early", 30, run_uuid)
            is None
        )
    assert status["status"] == "queued"
    assert int(status["retry_count"]) == 1
    assert status["run_after"] is not None
    assert job["status"] == "pending"
    assert int(job["attempts"]) == 1
    assert job["run_after"] is not None
    assert report["processing_jobs_retry_scheduled"] == 1

    with import_connection(settings) as connection:
        connection.execute(
            """
            UPDATE import_message_processing_status
            SET run_after = now() - interval '1 second' WHERE status_uuid = ?
            """,
            (status["status_uuid"],),
        )
        connection.execute(
            """
            UPDATE jobs SET run_after = now() - interval '1 second'
            WHERE job_uuid = ?
            """,
            (status["job_uuid"],),
        )
        connection.commit()
    assert worker.process_one_claimed_item(worker_id="pg-retry-b", import_run_uuid=run_uuid)
    with import_connection(settings) as connection:
        final_status = connection.execute(
            """
            SELECT status, retry_count FROM import_message_processing_status
            WHERE status_uuid = ?
            """,
            (status["status_uuid"],),
        ).fetchone()
        final_job = connection.execute(
            "SELECT status, attempts FROM jobs WHERE job_uuid = ?",
            (status["job_uuid"],),
        ).fetchone()
    assert final_status["status"] == "succeeded"
    assert int(final_status["retry_count"]) == 1
    assert final_job["status"] == "succeeded"
    assert int(final_job["attempts"]) == 2


@pytest.mark.skipif(not os.getenv("MEMORIST_POSTGRES_DSN"), reason="requires real PostgreSQL")
def test_full_postgres_restart_recovers_expired_claim_and_finishes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _pg_settings(tmp_path)
    run_uuid = _pg_committed_run(settings, tmp_path, monkeypatch, messages=2)
    with import_connection(settings) as connection:
        claimed = ImportMessageProcessor(connection, settings).claim_next(
            "pg-dead-process", 1, run_uuid
        )
        assert claimed is not None
        connection.execute(
            """
            UPDATE import_message_processing_status
            SET lease_expires_at = now() - interval '1 second'
            WHERE status_uuid = ?
            """,
            (claimed["status_uuid"],),
        )
        connection.commit()

    restarted = ImportReconstructionWorkerService(settings)
    restarted.start()
    try:
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            with import_connection(settings) as connection:
                run = connection.execute(
                    "SELECT status FROM import_runs WHERE import_run_uuid = ?",
                    (run_uuid,),
                ).fetchone()
                if run["status"] == "fully_reconstructed":
                    break
            time.sleep(0.05)
        else:
            raise AssertionError("PostgreSQL restart recovery did not finish")
    finally:
        restarted.stop()

    with import_connection(settings) as connection:
        duplicates = connection.execute(
            """
            SELECT processing_identity_hash, COUNT(*) AS copies
            FROM memory_processing_runs
            GROUP BY processing_identity_hash HAVING COUNT(*) > 1
            """
        ).fetchall()
        statuses = connection.execute(
            """
            SELECT status FROM import_message_processing_status
            WHERE import_run_uuid = ?
              AND processing_mode = 'full_memory_reconstruction'
              AND processing_identity_hash IS NOT NULL
              AND job_uuid IS NOT NULL
            """,
            (run_uuid,),
        ).fetchall()
    assert duplicates == []
    assert all(row["status"] in {"succeeded", "already_processed"} for row in statuses)


def test_full_postgres_import_connection_does_not_open_sqlite(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        runtime_profile="full",
        canonical_store="postgres",
        postgres_dsn="postgresql://example.invalid/memorist",
        db_path="/tmp/should-not-open.sqlite3",
        allow_full_graph_degraded=True,
        hot_scheduler="in_memory",
    )
    import memcore.imports.runtime as runtime

    def fail_sqlite(_path: str) -> None:
        raise AssertionError("SQLite connect(settings.db_path) must not be used in Full Mode")

    monkeypatch.setattr(runtime, "connect", fail_sqlite)
    with pytest.raises(Exception) as exc, runtime.import_connection(settings):
        pass
    assert "SQLite connect" not in str(exc.value)


def test_unsupported_import_runtime_fails_explicitly() -> None:
    settings = Settings.model_construct(
        runtime_profile="dev",
        canonical_store="sqlite",
        postgres_dsn=None,
        db_path="unused",
        object_store_path="unused",
    )
    with (
        pytest.raises(RuntimeError, match="unsupported import runtime"),
        import_connection(settings),
    ):
        pass
