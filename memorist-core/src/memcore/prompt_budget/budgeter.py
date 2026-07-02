from __future__ import annotations

import sqlite3
from typing import Any

from memcore.attachments.budget import AttachmentBudgetDecision, compute_attachment_budget
from memcore.config import Settings
from memcore.prompt_budget.model_context import ModelRegistry


def attachment_budget_decision(
    connection: sqlite3.Connection,
    settings: Settings,
    request: dict[str, Any],
) -> AttachmentBudgetDecision:
    registry = ModelRegistry(connection)
    model_name = request.get("target_model")
    provider = request.get("model_provider")
    entry = (
        registry.resolve(str(model_name), str(provider) if provider else None)
        if model_name
        else None
    )
    model_context_window = request.get("model_context_window")
    if model_context_window is None and entry is not None:
        model_context_window = entry.get("context_window")
    return compute_attachment_budget(
        mode=str(request.get("retrieval_mode") or settings.retrieval_mode),
        requested_budget=request.get("token_budget"),
        target_model=str(model_name) if model_name else None,
        model_context_window=int(model_context_window) if model_context_window else None,
        recent_conversation_text=request.get("recent_conversation_text"),
        estimated_recent_conversation_tokens=request.get("estimated_recent_conversation_tokens"),
        max_tokens=settings.attachment_max_tokens,
        context_ratio=settings.attachment_context_ratio,
        min_tokens=settings.attachment_min_tokens,
        reserved_completion_tokens=settings.attachment_reserved_completion_tokens,
        fallback_context_window=settings.unknown_model_context_window,
        safety_margin_tokens=settings.attachment_safety_margin_tokens,
    )
