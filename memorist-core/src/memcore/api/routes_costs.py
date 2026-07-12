from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from fastapi import APIRouter

from memcore.config import get_settings
from memcore.model_control.repository import ModelControlRepository
from memcore.storage.sqlite import connect

router = APIRouter(prefix="/memcore/costs", tags=["costs"])


@router.get("/model-roles", response_model=None)
def model_role_costs() -> dict[str, Any]:
    with _connection() as connection:
        usage = ModelControlRepository(connection).usage_summary()
        return {
            "status": "ok",
            "currency": "USD",
            "local_compute_note": (
                "Local models have no provider billing; local compute still has a cost."
            ),
            "usage": usage,
        }


@contextmanager
def _connection() -> Iterator[Any]:
    settings = get_settings()
    connection = connect(settings.db_path)
    try:
        yield connection
    finally:
        connection.close()
