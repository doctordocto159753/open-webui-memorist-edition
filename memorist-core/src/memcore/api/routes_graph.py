from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from memcore.config import get_settings
from memcore.graph.service import GraphProjectionService

router = APIRouter(prefix="/memcore/graph", tags=["Graph"])


@router.get("/status", response_model=None)
def graph_status() -> dict[str, Any]:
    return GraphProjectionService(get_settings()).status()


@router.get("/diagnostics", response_model=None)
def graph_diagnostics() -> dict[str, Any]:
    return GraphProjectionService(get_settings()).diagnostics()


@router.post("/project-pending", response_model=None)
def graph_project_pending() -> dict[str, Any]:
    return GraphProjectionService(get_settings()).project_pending().__dict__


@router.post("/rebuild", response_model=None)
def graph_rebuild() -> dict[str, Any]:
    return GraphProjectionService(get_settings()).rebuild()
