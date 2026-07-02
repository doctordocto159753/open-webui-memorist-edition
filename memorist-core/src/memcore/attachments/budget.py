from dataclasses import dataclass
from math import floor

from memcore.models import RetrievalMode

MODE_LIMITS = {
    RetrievalMode.LITE: 700,
    RetrievalMode.STANDARD: 1800,
    RetrievalMode.FULL: 3200,
    RetrievalMode.DEBUG: 2200,
}

KNOWN_CONTEXT_WINDOWS = {
    "llama3": 8192,
    "llama3.1": 131072,
    "llama3.2": 131072,
    "mistral": 8192,
    "qwen2.5": 32768,
    "gpt-4o-mini": 128000,
}


@dataclass(frozen=True)
class AttachmentBudgetDecision:
    effective_token_budget: int
    attachment_mode: str
    budget_reason: str
    token_estimation_method: str
    warnings: list[str]


def conservative_token_count(text: str) -> int:
    return max(1, (len(text) + 3) // 4)


def budget_for_mode(mode: RetrievalMode | str, requested_budget: int) -> int:
    mode_budget = MODE_LIMITS[RetrievalMode(mode)]
    return min(mode_budget, requested_budget)


def compute_attachment_budget(
    *,
    mode: RetrievalMode | str,
    requested_budget: int | None,
    target_model: str | None,
    model_context_window: int | None,
    recent_conversation_text: str | None,
    estimated_recent_conversation_tokens: int | None,
    max_tokens: int,
    context_ratio: float,
    min_tokens: int,
    reserved_completion_tokens: int,
    fallback_context_window: int,
    safety_margin_tokens: int,
) -> AttachmentBudgetDecision:
    warnings: list[str] = []
    context_window, context_reason = _resolve_context_window(
        target_model,
        model_context_window,
        fallback_context_window,
    )
    if context_reason:
        warnings.append(context_reason)
    recent_tokens, token_method = _estimate_recent_tokens(
        recent_conversation_text,
        estimated_recent_conversation_tokens,
    )
    ratio_budget = floor(context_window * context_ratio)
    remaining_budget = (
        context_window - recent_tokens - reserved_completion_tokens - safety_margin_tokens
    )
    mode_budget = MODE_LIMITS[RetrievalMode(mode)]
    request_cap = requested_budget if requested_budget is not None else max_tokens
    effective_budget = max(
        0,
        min(max_tokens, request_cap, ratio_budget, remaining_budget, mode_budget),
    )
    if effective_budget < min_tokens:
        return AttachmentBudgetDecision(
            effective_token_budget=0,
            attachment_mode="disabled",
            budget_reason=(
                "effective budget below minimum useful attachment budget "
                f"({effective_budget} < {min_tokens})"
            ),
            token_estimation_method=token_method,
            warnings=warnings,
        )
    return AttachmentBudgetDecision(
        effective_token_budget=effective_budget,
        attachment_mode=str(RetrievalMode(mode).value),
        budget_reason=(
            "min(max_tokens, request_cap, ratio_budget, remaining_budget, mode_cap) = "
            f"min({max_tokens}, {request_cap}, {ratio_budget}, {remaining_budget}, {mode_budget})"
        ),
        token_estimation_method=token_method,
        warnings=warnings,
    )


def _resolve_context_window(
    target_model: str | None,
    model_context_window: int | None,
    fallback_context_window: int,
) -> tuple[int, str | None]:
    if model_context_window is not None and model_context_window > 0:
        return model_context_window, None
    if target_model:
        normalized = target_model.lower()
        for prefix, window in KNOWN_CONTEXT_WINDOWS.items():
            if normalized.startswith(prefix):
                return window, None
    return fallback_context_window, "missing_model_context_window_used_fallback"


def _estimate_recent_tokens(
    recent_conversation_text: str | None,
    estimated_recent_conversation_tokens: int | None,
) -> tuple[int, str]:
    if estimated_recent_conversation_tokens is not None:
        return max(0, estimated_recent_conversation_tokens), "provided_estimate"
    if recent_conversation_text:
        return conservative_token_count(recent_conversation_text), "conservative_chars_div_4"
    return 0, "no_recent_conversation_supplied"
