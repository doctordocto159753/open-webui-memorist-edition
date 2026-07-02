from __future__ import annotations

from pydantic import BaseModel, Field

from memcore.models import ModelRole


class PromptMetadata(BaseModel):
    prompt_id: str
    prompt_version: str
    stage: str
    allowed_model_roles: list[ModelRole] = Field(min_length=1)
    input_schema_version: str = "1.0"
    output_schema_version: str = "1.0"
    requires_evidence: bool = True
    allows_remote_provider: bool = True
    blocking_path_allowed: bool = False
    default_timeout_ms: int = Field(default=8000, ge=1)
    is_legacy: bool = False

    @property
    def primary_model_role(self) -> ModelRole:
        return self.allowed_model_roles[0]


class ValidationResult(BaseModel):
    valid: bool
    prompt_id: str
    prompt_version: str
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
