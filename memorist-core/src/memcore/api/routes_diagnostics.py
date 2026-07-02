from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from fastapi import APIRouter

from memcore.config import get_settings
from memcore.full_mode import build_full_mode_status
from memcore.graph.service import GraphProjectionService
from memcore.storage.canonical.factory import create_canonical_store
from memcore.storage.migrations import apply_migrations
from memcore.storage.sqlite import connect
from memcore.storage.write_actor import get_write_actor

router = APIRouter(prefix="/memcore/diagnostics", tags=["Diagnostics"])


@router.get("/write-actor", response_model=None)
def write_actor_diagnostics() -> dict[str, Any]:
    settings = get_settings()
    return get_write_actor(settings.db_path).diagnostics()


@router.get("/daily", response_model=None)
def daily_diagnostics() -> dict[str, Any]:
    settings = get_settings()
    profile = settings.runtime_summary()
    full_mode_status = build_full_mode_status(settings)
    store = create_canonical_store(settings)
    capabilities = store.capabilities().model_dump()
    if settings.canonical_store == "postgres":
        health = store.health()
        graph = GraphProjectionService(settings).diagnostics()
        return {
            **profile,
            **full_mode_status,
            "mode": settings.retrieval_mode,
            "health": {
                "canonical_store": health.model_dump(mode="json"),
                "graph_backend": settings.graph_backend,
                "graph": graph,
                "openwebui_adapter": "ok" if settings.enable_openwebui_adapter else "disabled",
            },
            "capabilities": capabilities,
            "queues": {
                "jobs_pending": None,
                "import_paused": None,
            },
            "storage": {
                "postgres_schema_version": health.schema_version,
                "object_store_size_mb": _directory_size_mb(Path(settings.object_store_path)),
            },
            "warnings": profile["profile_warnings"]
            + ([] if health.ok else ["canonical_store_unhealthy"]),
        }

    writer = get_write_actor(settings.db_path).diagnostics()
    with _connection() as connection:
        jobs_pending = connection.execute(
            "SELECT COUNT(*) FROM jobs WHERE status = 'pending'"
        ).fetchone()[0]
        import_paused = connection.execute(
            "SELECT COUNT(*) FROM import_runs WHERE status = 'paused'"
        ).fetchone()[0]
        last_preflight = connection.execute(
            """
            SELECT event_type, created_at
            FROM preflight_events
            ORDER BY created_at DESC
            LIMIT 1
            """
        ).fetchone()
    db_path = Path(settings.db_path)
    object_store = Path(settings.object_store_path)
    warnings = []
    if int(writer["queue_depth"]) >= settings.import_max_write_queue_depth:
        warnings.append("write_queue_depth_high")
    return {
        **profile,
        **full_mode_status,
        "mode": settings.retrieval_mode,
        "health": {
            "canonical_store": store.health().model_dump(mode="json"),
            "sqlite": "ok" if db_path.exists() else "not_created",
            "write_actor": "ok" if writer["started"] else "idle",
            "job_queue": "ok",
            "graph_backend": settings.graph_backend,
            "openwebui_adapter": "ok" if settings.enable_openwebui_adapter else "disabled",
        },
        "capabilities": capabilities,
        "preflight": {
            "last_status": last_preflight["event_type"] if last_preflight else None,
            "p95_latency_ms": None,
            "timeouts_24h": 0,
            "failed_open_24h": 0,
        },
        "queues": {
            "write_depth": writer["queue_depth"],
            "jobs_pending": int(jobs_pending),
            "import_paused": int(import_paused) > 0,
        },
        "storage": {
            "sqlite_size_mb": (
                round(db_path.stat().st_size / 1_000_000, 3) if db_path.exists() else 0
            ),
            "object_store_size_mb": _directory_size_mb(object_store),
        },
        "warnings": warnings,
    }


@contextmanager
def _connection() -> Iterator[Any]:
    settings = get_settings()
    connection = connect(settings.db_path)
    try:
        apply_migrations(connection)
        yield connection
    finally:
        connection.close()


def _directory_size_mb(path: Path) -> float:
    if not path.exists():
        return 0.0
    total = sum(item.stat().st_size for item in path.rglob("*") if item.is_file())
    return round(total / 1_000_000, 3)
