from __future__ import annotations

from hashlib import sha256
from time import perf_counter

from memcore.model_control.providers.base import (
    EmbeddingResponse,
    ProviderHealth,
    StructuredCompletionResponse,
    TokenEstimate,
    heuristic_token_count,
)


class DeterministicProvider:
    provider_type = "deterministic"

    def __init__(self, model_name: str = "deterministic-local") -> None:
        self.model_name = model_name

    def health_check(self, timeout_seconds: float = 1.0) -> ProviderHealth:
        started = perf_counter()
        return ProviderHealth(
            status="ok",
            provider_type=self.provider_type,
            model_name=self.model_name,
            latency_ms=_elapsed_ms(started),
            local_only_safe=True,
            dns_or_host_reachable="not_applicable",
            tcp_or_http_reachable="not_applicable",
            authentication_status="not_applicable",
            model_status="available",
            chat_completion_status="supported",
            structured_output_status="supported",
            role_compatibility_status="compatible",
            overall_status="ok",
            detail_sanitized="deterministic local provider",
            recommended_action="No action required.",
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
        started = perf_counter()
        digest = sha256(prompt.encode("utf-8")).hexdigest()
        return StructuredCompletionResponse(
            output_ijson={"provider": self.provider_type, "digest": digest, "status": "ok"},
            input_tokens=heuristic_token_count(prompt),
            output_tokens=8,
            latency_ms=_elapsed_ms(started),
        )

    def embed(self, texts: list[str], timeout_seconds: float = 1.0) -> EmbeddingResponse:
        started = perf_counter()
        vectors = [_char_buckets(text) for text in texts]
        return EmbeddingResponse(
            vectors=vectors,
            input_tokens=sum(heuristic_token_count(text) for text in texts),
            latency_ms=_elapsed_ms(started),
        )


def _char_buckets(text: str) -> list[float]:
    buckets = [0.0] * 16
    for char in text.lower():
        buckets[ord(char) % len(buckets)] += 1.0
    return buckets


def _elapsed_ms(started: float) -> int:
    return int((perf_counter() - started) * 1000)
