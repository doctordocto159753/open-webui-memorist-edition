from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel, Field


class ProviderHealth(BaseModel):
    status: str
    provider_type: str
    model_name: str
    latency_ms: int = Field(ge=0)
    local_only_safe: bool
    detail: str | None = None


class TokenEstimate(BaseModel):
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    embedding_count: int = Field(default=0, ge=0)
    estimated_cost: float = Field(default=0.0, ge=0.0)
    currency: str = "USD"
    method: str = "heuristic"


class StructuredCompletionResponse(BaseModel):
    output_ijson: dict[str, Any]
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    latency_ms: int = Field(ge=0)


class EmbeddingResponse(BaseModel):
    vectors: list[list[float]]
    input_tokens: int = Field(ge=0)
    latency_ms: int = Field(ge=0)


class ModelProvider(Protocol):
    provider_type: str
    model_name: str

    def health_check(self, timeout_seconds: float = 1.0) -> ProviderHealth: ...

    def estimate_tokens(self, input_text: str = "", output_text: str = "") -> TokenEstimate: ...

    def complete_structured(
        self,
        prompt: str,
        timeout_seconds: float = 1.0,
    ) -> StructuredCompletionResponse: ...

    def embed(self, texts: list[str], timeout_seconds: float = 1.0) -> EmbeddingResponse: ...


def heuristic_token_count(text: str) -> int:
    return max(1, (len(text) + 3) // 4) if text else 0
