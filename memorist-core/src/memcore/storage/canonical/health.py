from typing import Literal

from pydantic import BaseModel


class StoreHealth(BaseModel):
    store_kind: Literal["sqlite", "postgres"]
    ok: bool
    status: str
    schema_version: int | None = None
    error_sanitized: str | None = None
