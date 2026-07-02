from typing import Any

from pydantic import BaseModel, Field

from memcore.models import MemoryBlockType, new_uuid, utc_now


class BlockSource(BaseModel):
    memory_uuid: str
    memory_version_uuid: str
    canonical_key: str
    memory_type: str
    scope_type: str
    scope_uuid: str | None
    normalized_text: str
    source_authority: str
    current: bool
    confidence: float
    valid_from: str | None = None
    valid_until: str | None = None
    sensitivity_class: str
    user_confirmed: bool = False
    inclusion_reason: str = "eligible_current_source"


class BlockBuildRun(BaseModel):
    block_build_run_uuid: str = Field(default_factory=new_uuid)
    block_uuid: str
    prompt_execution_uuid: str | None = None
    builder_version: str
    source_snapshot_hash: str
    status: str = "pending"
    trigger_type: str
    started_at: str | None = None
    completed_at: str | None = None
    raw_output_ijson: str | None = None
    validation_errors_ijson: str | None = None
    error_text: str | None = None
    created_at: str = Field(default_factory=utc_now)
    schema_version: int = 1


class BlockVersion(BaseModel):
    block_version_uuid: str = Field(default_factory=new_uuid)
    block_uuid: str
    prompt_execution_uuid: str | None = None
    version_number: int = Field(ge=1)
    value: str
    structured_value_ijson: str | None = None
    char_count: int = Field(ge=0)
    token_count: int = Field(ge=0)
    source_snapshot_hash: str
    block_build_run_uuid: str | None = None
    change_reason: str | None = None
    created_by: str
    created_at: str = Field(default_factory=utc_now)
    schema_version: int = 1


class BuildResult(BaseModel):
    block_uuid: str
    block_version_uuid: str
    version_number: int
    source_snapshot_hash: str
    value: str
    omitted_sources: list[dict[str, Any]]


class CoverageReport(BaseModel):
    block_uuid: str
    source_coverage: float
    required_item_coverage: float
    constraint_coverage: float
    unsupported_assertion_count: int
    conflict_preservation: bool
    char_count: int
    token_count: int
    omitted_sources: list[dict[str, Any]]
    stale: bool


BLOCK_TYPE_LABELS = {block_type.value: block_type.value for block_type in MemoryBlockType}
