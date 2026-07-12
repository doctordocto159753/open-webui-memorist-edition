from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from memcore.config import get_settings
from memcore.prompt_budget.budgeter import attachment_budget_decision
from memcore.prompt_budget.model_context import ModelRegistry
from memcore.storage.sqlite import connect

router = APIRouter(prefix="/memcore", tags=["Budget"])


class AttachmentBudgetRequest(BaseModel):
    retrieval_mode: str = "standard"
    token_budget: int | None = Field(default=None, ge=1)
    target_model: str | None = None
    model_provider: str | None = None
    model_context_window: int | None = Field(default=None, ge=1)
    recent_conversation_text: str | None = None
    estimated_recent_conversation_tokens: int | None = Field(default=None, ge=0)


class ModelRegistryRequest(BaseModel):
    provider: str = Field(min_length=1)
    model_name: str = Field(min_length=1)
    model_aliases: list[str] = Field(default_factory=list)
    context_window: int | None = Field(default=None, ge=1)
    max_output_tokens_default: int | None = Field(default=None, ge=1)
    tokenizer_family: str = "heuristic"
    supports_token_counting: bool = False
    last_verified_at: str | None = None
    source: str = "user_configured"
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)


class ModelRegistryPatchRequest(BaseModel):
    provider: str | None = Field(default=None, min_length=1)
    model_name: str | None = Field(default=None, min_length=1)
    model_aliases: list[str] | None = None
    context_window: int | None = Field(default=None, ge=1)
    max_output_tokens_default: int | None = Field(default=None, ge=1)
    tokenizer_family: str | None = None
    supports_token_counting: bool | None = None
    last_verified_at: str | None = None
    source: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)


@router.post("/budget/attachment", response_model=None)
def calculate_attachment_budget(request: AttachmentBudgetRequest) -> dict[str, Any]:
    settings = get_settings()
    with _connection() as connection:
        decision = attachment_budget_decision(connection, settings, request.model_dump())
        return {
            "effective_token_budget": decision.effective_token_budget,
            "attachment_mode": decision.attachment_mode,
            "budget_reason": decision.budget_reason,
            "token_estimation_method": decision.token_estimation_method,
            "warnings": decision.warnings,
        }


@router.get("/model-registry", response_model=None)
def list_model_registry() -> dict[str, Any]:
    with _connection() as connection:
        return {"items": ModelRegistry(connection).list_entries()}


@router.post("/model-registry", response_model=None)
def create_model_registry_entry(request: ModelRegistryRequest) -> dict[str, Any]:
    with _connection() as connection:
        return ModelRegistry(connection).create_or_update(**request.model_dump())


@router.patch("/model-registry/{model_profile_uuid}", response_model=None)
def patch_model_registry_entry(
    model_profile_uuid: str,
    request: ModelRegistryPatchRequest,
) -> dict[str, Any]:
    with _connection() as connection:
        try:
            return ModelRegistry(connection).patch(
                model_profile_uuid,
                request.model_dump(exclude_unset=True),
            )
        except ValueError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error


@contextmanager
def _connection() -> Iterator[Any]:
    settings = get_settings()
    connection = connect(settings.db_path)
    try:
        yield connection
    finally:
        connection.close()
