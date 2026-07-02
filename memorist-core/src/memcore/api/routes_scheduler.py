from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from memcore.config import get_settings
from memcore.scheduler.hot_scheduler import get_hot_scheduler

router = APIRouter(prefix="/memcore/scheduler", tags=["Scheduler"])


@router.get("/status", response_model=None)
def scheduler_status() -> dict[str, Any]:
    settings = get_settings()
    scheduler = get_hot_scheduler(settings)
    if scheduler is None:
        return {
            "enabled": False,
            "backend": settings.hot_scheduler,
            "runtime_profile": settings.runtime_profile,
            "canonical_store": settings.canonical_store,
        }
    status = scheduler.status()
    status.update(
        {
            "backend": settings.hot_scheduler,
            "runtime_profile": settings.runtime_profile,
            "canonical_store": settings.canonical_store,
        }
    )
    return status
