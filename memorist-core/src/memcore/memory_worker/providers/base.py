from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class ProviderResponse:
    output: dict[str, Any]
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    provider_response_id: str | None = None


class MemoryExtractionProvider(Protocol):
    def extract(self, *, system_prompt: str, input_payload: dict[str, Any]) -> ProviderResponse: ...
