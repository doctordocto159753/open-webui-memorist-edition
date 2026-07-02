from __future__ import annotations

from pydantic import BaseModel, Field

from memcore.models import JakobsonConfidence, MemorySignalRouteType


class RouteDecision(BaseModel):
    route_type: MemorySignalRouteType
    extractor_id: str
    priority: int = Field(ge=0, le=100)
    confidence: JakobsonConfidence
    reason: str
