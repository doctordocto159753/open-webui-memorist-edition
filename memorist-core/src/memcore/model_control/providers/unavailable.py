from __future__ import annotations

from time import perf_counter

from memcore.model_control.providers.base import (
    EmbeddingResponse,
    ProviderHealth,
    StructuredCompletionResponse,
    TokenEstimate,
    heuristic_token_count,
)


class UnavailableProvider:
    provider_type = "unknown"

    def __init__(
        self,
        model_name: str = "unknown",
        provider_type: str = "unknown",
        detail: str | None = None,
    ) -> None:
        self.model_name = model_name
        self.provider_type = provider_type or "unknown"
        self.detail = detail or f"unknown provider type: {self.provider_type}"

    def health_check(self, timeout_seconds: float = 1.0) -> ProviderHealth:
        started = perf_counter()
        return ProviderHealth(
            status="error",
            provider_type=self.provider_type,
            model_name=self.model_name,
            latency_ms=_elapsed_ms(started),
            local_only_safe=True,
            role_compatibility_status="incompatible",
            overall_status="misconfigured",
            detail_sanitized=self.detail,
            recommended_action="Select a supported provider type.",
        )

    def estimate_tokens(self, input_text: str = "", output_text: str = "") -> TokenEstimate:
        return TokenEstimate(
            input_tokens=heuristic_token_count(input_text),
            output_tokens=heuristic_token_count(output_text),
            estimated_cost=0.0,
        )

    def complete_structured(
        self,
        prompt: str,
        timeout_seconds: float = 1.0,
    ) -> StructuredCompletionResponse:
        raise RuntimeError(self.detail)

    def embed(self, texts: list[str], timeout_seconds: float = 1.0) -> EmbeddingResponse:
        raise RuntimeError(self.detail)


def _elapsed_ms(started: float) -> int:
    return int((perf_counter() - started) * 1000)
