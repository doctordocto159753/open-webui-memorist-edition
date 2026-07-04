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
