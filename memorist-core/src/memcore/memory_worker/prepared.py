from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PreparedJakobsonInference:
    """Validated provider output produced without canonical database writes."""

    message_uuid: str
    model_role: str
    model_profile_uuid: str | None
    provider_type: str
    model_name: str
    processing_identity_hash: str
    input_content_hash: str
    output: dict[str, Any]
    input_tokens: int
    output_tokens: int
    latency_ms: int
