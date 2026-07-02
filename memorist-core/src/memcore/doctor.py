from __future__ import annotations

import json
from typing import Any

from memcore.config import get_settings
from memcore.graph.service import GraphProjectionService
from memcore.scheduler.hot_scheduler import get_hot_scheduler
from memcore.storage.canonical.factory import create_canonical_store


def build_doctor_report() -> dict[str, Any]:
    settings = get_settings()
    store = create_canonical_store(settings)
    graph = GraphProjectionService(settings)
    scheduler = get_hot_scheduler(settings)
    store_health = store.health()
    graph_status = graph.status()
    scheduler_status = (
        scheduler.status()
        if scheduler is not None
        else {"enabled": False, "backend": settings.hot_scheduler}
    )
    return {
        **settings.runtime_summary(),
        "canonical_store_health": store_health.model_dump(mode="json"),
        "canonical_store_capabilities": store.capabilities().model_dump(),
        "postgres": (
            {"health": store_health.model_dump(mode="json")}
            if settings.canonical_store == "postgres"
            else {"health": "not_required"}
        ),
        "falkordb": graph_status,
        "scheduler": scheduler_status,
        "job_lag": None,
        "import_status": None,
        "graph_projection_lag": None,
        "fallback_status": (
            "postgres_retrieval" if graph_status.get("status") == "degraded" else "normal"
        ),
    }


def main() -> None:
    print(json.dumps(build_doctor_report(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
