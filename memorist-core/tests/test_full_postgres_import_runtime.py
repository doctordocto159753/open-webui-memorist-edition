from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from uuid import uuid4
from zipfile import ZipFile

import pytest

from memcore.config import Settings
from memcore.imports.processing import ImportMessageProcessor
from memcore.imports.runtime import import_connection, initialize_runtime_storage
from memcore.imports.service import ImportService
from memcore.imports.worker import ImportReconstructionWorkerService


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
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, messages: int = 4
) -> str:
    import memcore.imports.service as service_module

    monkeypatch.setattr(service_module, "get_settings", lambda: settings)
    initialize_runtime_storage(settings)
    with import_connection(settings) as connection:
        service = ImportService(connection, settings.object_store_path)
        run = service.upload(str(_pg_archive(tmp_path, messages=messages)))
        run_uuid = str(run["import_run_uuid"])
        service.inspect(run_uuid)
        service.reconstruct(run_uuid)
        service.dry_run(run_uuid, "full_memory_reconstruction")
        service.commit(run_uuid, "full_memory_reconstruction")
        return run_uuid


def _pg_archive(path: Path, messages: int = 4) -> Path:
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
                    "parts": [f"Durable PostgreSQL fact {unique} {index}."],
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


@pytest.mark.skipif(not os.getenv("MEMORIST_POSTGRES_DSN"), reason="requires real PostgreSQL")
def test_full_postgres_concurrent_process_endpoint_does_not_duplicate_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _pg_settings(tmp_path)
    run_uuid = _pg_committed_run(settings, tmp_path, monkeypatch, messages=4)
    results: list[dict[str, object]] = []

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
    first.join(timeout=20)
    second.join(timeout=20)
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
                   COUNT(*) FILTER (WHERE status IN ('succeeded', 'already_processed')) AS done
            FROM import_message_processing_status
            WHERE import_run_uuid = ?
            """,
            (run_uuid,),
        ).fetchone()
    assert duplicates == []
    assert int(statuses["done"]) <= int(statuses["total"])


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
