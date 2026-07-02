from __future__ import annotations

from dataclasses import dataclass

from memcore.attachments.budget import conservative_token_count


@dataclass(frozen=True)
class TokenEstimate:
    tokens: int
    method: str
    confidence: float


def estimate_tokens(text: str | None, provided: int | None = None) -> TokenEstimate:
    if provided is not None:
        return TokenEstimate(max(0, provided), "provided_estimate", 0.9)
    if not text:
        return TokenEstimate(0, "empty", 0.8)
    return TokenEstimate(conservative_token_count(text), "conservative_chars_div_4", 0.55)
