from __future__ import annotations

import json
import os
import time
from pathlib import Path
from zipfile import ZipFile

import pytest

from memcore.config import Settings
from memcore.imports.runtime import import_connection, initialize_runtime_storage
from memcore.imports.service import ImportService
from memcore.imports.worker import ImportReconstructionWorkerService


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
