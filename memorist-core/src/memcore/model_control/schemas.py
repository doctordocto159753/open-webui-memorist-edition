from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, model_validator

from memcore.model_control.security import (
    endpoint_contains_secret,
    endpoint_is_local,
    reject_raw_secrets,
    validate_secret_strategy,
)
from memcore.models import ModelRole


class ProviderType(StrEnum):
    DISABLED = "disabled"
    DETERMINISTIC = "deterministic"
    OPENAI_COMPATIBLE = "openai_compatible"
    OPENAI_COMPATIBLE_LLM = "openai_compatible_llm"
    OPENAI_COMPATIBLE_EMBEDDING = "openai_compatible_embedding"
    OLLAMA = "ollama"
    OLLAMA_LLM = "ollama_llm"
    OLLAMA_EMBEDDING = "ollama_embedding"
    LOCAL_EMBEDDING = "local_embedding"
    UNKNOWN = "unknown"


class ModelProfileCreate(BaseModel):
    profile_name: str | None = Field(default=None, min_length=1)
    provider_type: ProviderType = ProviderType.DISABLED
    provider_name: str | None = None
    model_name: str = Field(default="disabled", min_length=1)
    role: ModelRole
    endpoint_url: str | None = None
    endpoint_is_local: bool | None = None
    context_window: int | None = Field(default=None, ge=1)
    max_input_tokens: int | None = Field(default=None, ge=1)
    max_output_tokens: int | None = Field(default=None, ge=1)
    supports_structured_output: bool = False
    supports_json_mode: bool = False
    supports_embeddings: bool = False
    embedding_dimension: int | None = Field(default=None, ge=1)
    tokenizer_family: str | None = None
    quality_profile: str = "unknown"
    latency_profile: str = "unknown"
    quality_profile_data: dict[str, Any] | None = None
    latency_profile_data: dict[str, Any] | None = None
    cost_profile: dict[str, Any] | None = None
    privacy_profile: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None
    secret_strategy: str = "none"
    secret_env_var_name: str | None = None
    is_enabled: bool = True
    privacy_acknowledged: bool = False

    @model_validator(mode="after")
    def validate_policy(self) -> ModelProfileCreate:
        _validate_common_policy(self)
        return self


class ModelProfilePatch(BaseModel):
    profile_name: str | None = Field(default=None, min_length=1)
    provider_type: ProviderType | None = None
    provider_name: str | None = None
    model_name: str | None = Field(default=None, min_length=1)
    role: ModelRole | None = None
    endpoint_url: str | None = None
    endpoint_is_local: bool | None = None
    context_window: int | None = Field(default=None, ge=1)
    max_input_tokens: int | None = Field(default=None, ge=1)
    max_output_tokens: int | None = Field(default=None, ge=1)
    supports_structured_output: bool | None = None
    supports_json_mode: bool | None = None
    supports_embeddings: bool | None = None
    embedding_dimension: int | None = Field(default=None, ge=1)
    tokenizer_family: str | None = None
    quality_profile: str | None = None
    latency_profile: str | None = None
    quality_profile_data: dict[str, Any] | None = None
    latency_profile_data: dict[str, Any] | None = None
    cost_profile: dict[str, Any] | None = None
    privacy_profile: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None
    secret_strategy: str | None = None
    secret_env_var_name: str | None = None
    is_enabled: bool | None = None
    privacy_acknowledged: bool | None = None

    @model_validator(mode="after")
    def validate_policy(self) -> ModelProfilePatch:
        _validate_common_policy(self)
        return self


class ModelRoleDefaultSet(BaseModel):
    role: ModelRole
    model_profile_uuid: str
    workspace_uuid: str | None = None
    project_uuid: str | None = None


class PrivacyAcknowledgementRequest(BaseModel):
    model_profile_uuid: str
    acknowledged_risk_level: str = Field(min_length=1)
    acknowledged_data_sent: dict[str, Any] = Field(default_factory=dict)


class ProfileTestRequest(BaseModel):
    timeout_ms: int = Field(default=1000, ge=1, le=10_000)


class CostEstimateRequest(BaseModel):
    role: ModelRole | None = None
    model_profile_uuid: str | None = None
    input_text: str = ""
    output_text: str = ""
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    embedding_count: int = Field(default=0, ge=0)


class UsageEventCreate(BaseModel):
    role: ModelRole
    stage: str = Field(min_length=1)
    model_profile_uuid: str | None = None
    workspace_uuid: str | None = None
    project_uuid: str | None = None
    session_uuid: str | None = None
    message_uuid: str | None = None
    unit_uuid: str | None = None
    annotation_uuid: str | None = None
    import_run_uuid: str | None = None
    attachment_uuid: str | None = None
    job_uuid: str | None = None
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    embedding_count: int = Field(default=0, ge=0)
    estimated_cost: float | None = Field(default=None, ge=0.0)
    latency_ms: int | None = Field(default=None, ge=0)
    status: str = Field(default="ok", min_length=1)
    error_class: str | None = None
    error_message_sanitized: str | None = None


def _validate_common_policy(model: ModelProfileCreate | ModelProfilePatch) -> None:
    if endpoint_contains_secret(model.endpoint_url):
        raise ValueError("endpoint_url must not contain credentials, API keys, or tokens")
    if model.secret_strategy is not None:
        validate_secret_strategy(model.secret_strategy, model.secret_env_var_name)
    reject_raw_secrets(model.cost_profile, "cost_profile")
    reject_raw_secrets(model.privacy_profile, "privacy_profile")
    reject_raw_secrets(model.quality_profile_data, "quality_profile_data")
    reject_raw_secrets(model.latency_profile_data, "latency_profile_data")
    reject_raw_secrets(model.metadata, "metadata")
    if model.endpoint_is_local is False and model.provider_type in {
        ProviderType.DISABLED,
        ProviderType.DETERMINISTIC,
        ProviderType.LOCAL_EMBEDDING,
        ProviderType.UNKNOWN,
    }:
        raise ValueError("disabled/deterministic providers must be local-only")
    if model.endpoint_is_local is None and model.endpoint_url:
        model.endpoint_is_local = endpoint_is_local(model.endpoint_url)
