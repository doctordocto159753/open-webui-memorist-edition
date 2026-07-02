from __future__ import annotations

from typing import Any, Literal

from memcore.config import Settings
from memcore.graph.service import GraphProjectionService

FullModeCertification = Literal["passed", "failed", "not_run", "degraded"]


def build_full_mode_status(settings: Settings) -> dict[str, Any]:
    graph = GraphProjectionService(settings).status()
    graph_status = str(graph.get("status", "disabled"))
    certification = _certification_status(settings, graph_status)
    return {
        "runtime_profile": settings.runtime_profile,
        "canonical_store": settings.canonical_store,
        "graph_backend": settings.graph_backend,
        "graph_status": graph_status,
        "scheduler": settings.hot_scheduler,
        "full_mode_certification": certification,
    }


def _certification_status(
    settings: Settings,
    graph_status: str,
) -> FullModeCertification:
    if settings.runtime_profile != "full":
        return "not_run"
    if settings.canonical_store != "postgres":
        return "failed"
    if graph_status != "ok":
        return "degraded"
    return "not_run"
