from __future__ import annotations

from pathlib import Path


def test_full_postgres_process_message_uses_production_pipeline() -> None:
    source = Path("memorist-core/src/memcore/api/routes_memory.py").read_text(encoding="utf-8")
    route_body = source.split("def process_message(message_uuid: str) -> dict[str, object]:", 1)[
        1
    ].split('\n\n@router.post("/graph-projection/run-once"', 1)[0]
    assert "PostgresMemoryWorkerPipeline" in route_body
    assert "_pg_process_message_smoke" not in route_body
    assert "full-smoke-v1" not in route_body


def test_full_postgres_import_routes_do_not_silently_open_sqlite() -> None:
    source = Path("memorist-core/src/memcore/api/routes_imports.py").read_text(encoding="utf-8")
    connection_body = source.split("def _connection() -> Iterator[Any]:", 1)[1]

    assert 'settings.runtime_profile == "full"' in connection_body
    assert "SQLite fallback is disabled" in connection_body
    assert "connect(settings.db_path)" in connection_body
    assert connection_body.index("_reject_full_mode_import(settings)") < connection_body.index(
        "connect(settings.db_path)"
    )
    commit_body = source.split("def import_commit(", 1)[1].split(
        "\n\n@router.get(\"/imports/{import_run_uuid}/progress\"",
        1,
    )[0]
    assert "_reject_full_mode_import(settings)" in commit_body
