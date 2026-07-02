from typing import Any

from fastapi import APIRouter

from memcore.config import get_settings
from memcore.full_mode import build_full_mode_status

router = APIRouter(prefix="/memcore", tags=["health"])


@router.get("/health", response_model=None)
def get_health() -> dict[str, Any]:
    settings = get_settings()
    return {
        "status": "ok",
        "service": "memorist-core",
        "local_only": settings.local_only,
        **settings.runtime_summary(),
        **build_full_mode_status(settings),
    }
