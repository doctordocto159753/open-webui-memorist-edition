from typing import Any

from fastapi import APIRouter

from memcore.config import get_effective_config, get_settings
from memcore.version import SCHEMA_VERSION, __version__

router = APIRouter(prefix="/memcore", tags=["config"])


@router.get("/version", response_model=None)
def get_version() -> dict[str, Any]:
    settings = get_settings()
    return {
        "service": "memorist-core",
        "version": __version__,
        "schema_version": SCHEMA_VERSION,
        **settings.runtime_summary(),
    }


@router.get("/config/effective", response_model=None)
def get_effective_configuration() -> dict[str, Any]:
    return get_effective_config(get_settings())
