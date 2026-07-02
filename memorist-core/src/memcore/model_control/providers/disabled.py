from __future__ import annotations

from time import perf_counter

from memcore.model_control.providers.base import (
    EmbeddingResponse,
    ProviderHealth,
    StructuredCompletionResponse,
    TokenEstimate,
    heuristic_token_count,
)


class DisabledProvider:
    provider_type = "disabled"

    def __init__(self, model_name: str = "disabled") -> None:
        self.model_name = model_name

    def health_check(self, timeout_seconds: float = 1.0) -> ProviderHealth:
        started = perf_counter()
        return ProviderHealth(
            status="disabled",
            provider_type=self.provider_type,
            model_name=self.model_name,
            latency_ms=_elapsed_ms(started),
            local_only_safe=True,
            detail="provider disabled",
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
        raise RuntimeError("disabled provider cannot generate completions")

    def embed(self, texts: list[str], timeout_seconds: float = 1.0) -> EmbeddingResponse:
        raise RuntimeError("disabled provider cannot generate embeddings")


def _elapsed_ms(started: float) -> int:
    return int((perf_counter() - started) * 1000)
