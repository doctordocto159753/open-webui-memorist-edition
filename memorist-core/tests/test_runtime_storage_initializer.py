from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from memcore.config import Settings
from memcore.imports.processing import ImportMessageProcessor
from memcore.imports.worker import ImportReconstructionWorkerService


def _settings(tmp_path: Path, *, profile: str = "lite", store: str = "sqlite") -> Settings:
    return Settings(
        runtime_profile=profile,
        canonical_store=store,
        postgres_dsn="postgresql://example.invalid/memorist" if store == "postgres" else None,
        db_path=str(tmp_path / "memorist.sqlite3"),
        object_store_path=str(tmp_path / "objects"),
        import_reconstruction_worker_enabled=False,
        allow_full_graph_degraded=True,
        hot_scheduler="in_memory",
    )


def test_lite_sqlite_initializer_runs_sqlite_migrations_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import memcore.imports.runtime as runtime

    calls: list[Any] = []

    def record_migration(connection: Any) -> None:
        calls.append(connection)

    def fail_postgres(_connection: Any) -> None:
        raise AssertionError("PostgreSQL migrations must not run for lite/sqlite")

    monkeypatch.setattr(runtime, "apply_migrations", record_migration)
    monkeypatch.setattr(runtime, "apply_postgres_migrations", fail_postgres)

    runtime.initialize_runtime_storage(_settings(tmp_path))

    assert len(calls) == 1


def test_full_postgres_initializer_runs_only_postgres_migrations_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import memcore.imports.runtime as runtime

    class FakeConnection:
        raw = object()

        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    fake_connection = FakeConnection()
    calls: list[Any] = []

    def fail_sqlite(_connection: Any) -> None:
        raise AssertionError("SQLite migrations must not run for full/postgres")

    def record_migration(connection: Any) -> None:
        calls.append(connection)

    monkeypatch.setattr(runtime, "connect_compat", lambda _dsn: fake_connection)
    monkeypatch.setattr(runtime, "apply_migrations", fail_sqlite)
    monkeypatch.setattr(runtime, "apply_postgres_migrations", record_migration)

    runtime.initialize_runtime_storage(_settings(tmp_path, profile="full", store="postgres"))

    assert calls == [fake_connection.raw]
    assert fake_connection.closed is True


def test_import_connection_does_not_run_migrations_after_startup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import memcore.imports.runtime as runtime

    settings = _settings(tmp_path)
    runtime.initialize_runtime_storage(settings)

    def fail_migration(_connection: Any) -> None:
        raise AssertionError("import_connection must not run migrations")

    monkeypatch.setattr(runtime, "apply_migrations", fail_migration)
    monkeypatch.setattr(runtime, "apply_postgres_migrations", fail_migration)

    with runtime.import_connection(settings) as connection:
        assert connection.execute("SELECT 1").fetchone()[0] == 1


def test_worker_polling_and_heartbeat_do_not_run_migrations_after_startup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import memcore.imports.runtime as runtime
    settings = _settings(tmp_path)
    runtime.initialize_runtime_storage(settings)

    def fail_migration(_connection: Any) -> None:
        raise AssertionError("worker runtime connections must not run migrations")

    monkeypatch.setattr(runtime, "apply_migrations", fail_migration)
    monkeypatch.setattr(runtime, "apply_postgres_migrations", fail_migration)

    service = ImportReconstructionWorkerService(settings)
    assert service.process_once("test-worker") is False

    with runtime.import_connection(settings) as connection:
        assert (
            ImportMessageProcessor(connection, settings).renew_lease(
                "missing-status", "test-worker", settings.import_reconstruction_lease_seconds
            )
            is False
        )


class _FakeRepository:
    def list_runs(self, limit: int) -> list[Any]:
        return []


class _FakeImportService:
    def __init__(self, _connection: Any, _object_store_path: str) -> None:
        self.repository = _FakeRepository()


def test_api_route_import_connection_does_not_run_migrations_after_startup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import memcore.api.routes_imports as routes_imports
    import memcore.config as config
    import memcore.imports.runtime as runtime

    settings = _settings(tmp_path)
    runtime.initialize_runtime_storage(settings)

    def fail_migration(_connection: Any) -> None:
        raise AssertionError("API import route connections must not run migrations")

    monkeypatch.setattr(runtime, "apply_migrations", fail_migration)
    monkeypatch.setattr(runtime, "apply_postgres_migrations", fail_migration)
    monkeypatch.setattr(routes_imports, "get_settings", lambda: settings)
    monkeypatch.setattr(config, "get_settings", lambda: settings)
    monkeypatch.setattr(routes_imports, "ImportService", _FakeImportService)

    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(routes_imports.router)

    with TestClient(app) as client:
        response = client.get("/memcore/imports")

    assert response.status_code == 200
    assert response.json() == {"items": []}


def test_create_app_initializes_runtime_before_worker_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import memcore.main as main

    events: list[str] = []
    settings = _settings(tmp_path)

    class FakeWorker:
        def __init__(self, _settings: Settings) -> None:
            events.append("worker_constructed")

        def start(self) -> None:
            events.append("worker_started")

        def stop(self) -> None:
            events.append("worker_stopped")

    monkeypatch.setattr(main, "get_settings", lambda: settings)
    monkeypatch.setattr(main, "initialize_runtime_storage", lambda _settings: events.append("init"))
    monkeypatch.setattr(main, "ImportReconstructionWorkerService", FakeWorker)

    app = main.create_app()
    with TestClient(app):
        pass

    assert events == ["init", "worker_constructed", "worker_started", "worker_stopped"]


def test_unsupported_runtime_store_pair_fails_explicitly() -> None:
    from memcore.imports.runtime import initialize_runtime_storage

    settings = Settings.model_construct(
        runtime_profile="lite",
        canonical_store="postgres",
        postgres_dsn="postgresql://example.invalid/memorist",
        db_path="unused",
        object_store_path="unused",
    )

    with pytest.raises(RuntimeError, match="unsupported import runtime"):
        initialize_runtime_storage(settings)
