from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class PreparedJakobsonInference:
    """Validated provider output produced without canonical database writes.

    ``output`` is always contract-valid (from the provider, a bounded repair,
    a documented canonicalization, or the deterministic fallback). The remaining
    fields carry the truthful audit of how it was produced so the pipeline can
    persist an accurate provider attempt row.
    """

    message_uuid: str
    model_role: str
    model_profile_uuid: str | None
    provider_type: str
    model_name: str
    processing_identity_hash: str
    input_content_hash: str
    profile_fingerprint: str
    output: dict[str, Any]
    input_tokens: int
    output_tokens: int
    latency_ms: int
    prompt_version: str = "2.0"
    called_provider: bool = False
    provider_output_valid: bool = False
    canonicalized: bool = False
    repair_attempted: bool = False
    repair_succeeded: bool = False
    fallback_used: bool = False
    fallback_reason: str | None = None
    capability_mode: str = "deterministic"
    provider_response_id: str | None = None
    parse_status: str = "not_called"
    validation_error_paths: list[str] = field(default_factory=list)
