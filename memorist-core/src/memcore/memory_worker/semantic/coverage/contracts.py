"""Frozen, persistence-neutral WP02 coverage domain contracts."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from memcore.memory_worker.prompts.contracts import SemanticAnalysisV1Output
from memcore.memory_worker.semantic_contract import BoundedContextItem

COVERAGE_PLAN_VERSION: Literal["memorist.semantic_candidate.coverage_plan.v1"] = (
    "memorist.semantic_candidate.coverage_plan.v1"
)
COVERAGE_PLANNER_VERSION = "memorist.semantic_candidate.coverage_planner.v1"


class _FrozenStrict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class CoverageDisposition(StrEnum):
    DURABLE_CANDIDATE = "durable_candidate"
    CONTEXT_ONLY = "context_only"
    TRANSIENT_INSTRUCTION = "transient_instruction"
    UNRESOLVED_REFERENCE = "unresolved_reference"
    REJECTED_BY_GATE = "rejected_by_gate"
    NEEDS_REVIEW = "needs_review"
    UNSUPPORTED = "unsupported"


class PersistedUnitAuthority(_FrozenStrict):
    """Persisted route/gate/annotation authority for one current text unit."""

    text_unit_uuid: Annotated[str, Field(min_length=1)]
    raw_start: Annotated[int, Field(ge=0)]
    raw_end: Annotated[int, Field(gt=0)]
    annotation_uuid: str | None
    gate_decision_uuid: str | None
    gate_decision: str | None
    route_uuid: str | None
    route_type: str | None
    route_status: str | None
    privacy_ceiling: Literal["normal", "sensitive", "secret"]
    privacy_storage_allowed: bool
    conflicting_authority: bool = False

    @model_validator(mode="after")
    def _ordered_span(self) -> PersistedUnitAuthority:
        if self.raw_start >= self.raw_end:
            raise ValueError("raw_start must be less than raw_end")
        return self


class CoveragePlannerInput(_FrozenStrict):
    message_uuid: Annotated[str, Field(min_length=1)]
    message_version_uuid: str | None
    message_role: Literal["user", "assistant", "tool", "system"]
    processing_run_uuid: Annotated[str, Field(min_length=1)]
    current_raw_text: str
    text_envelope: dict[str, Any]
    semantic_analysis: SemanticAnalysisV1Output
    accepted_unit_ids: tuple[str, ...]
    accepted_reference_indexes: tuple[int, ...]
    accepted_relation_indexes: tuple[int, ...]
    authorities: tuple[PersistedUnitAuthority, ...]
    semantic_prompt_execution_uuid: str | None
    semantic_contract_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    bounded_context_items: tuple[BoundedContextItem, ...]
    imported_record: bool = False
    route_mapping_version: str
    provenance_policy_version: str
    privacy_policy_version: str


class CoverageItem(_FrozenStrict):
    coverage_item_id: Annotated[str, Field(min_length=1)]
    semantic_unit_id: str | None
    raw_start: Annotated[int, Field(ge=0)]
    raw_end: Annotated[int, Field(gt=0)]
    disposition: CoverageDisposition
    reason_codes: tuple[str, ...]
    gate_decision_uuid: str | None
    route_uuid: str | None
    proposal_id: str | None


class CoveragePlan(_FrozenStrict):
    coverage_plan_version: Literal["memorist.semantic_candidate.coverage_plan.v1"]
    message_uuid: Annotated[str, Field(min_length=1)]
    raw_text_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    processing_run_uuid: Annotated[str, Field(min_length=1)]
    semantic_prompt_execution_uuid: str | None
    semantic_contract_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    status: Literal["complete", "abstain", "retain_raw_only", "needs_review"]
    items: tuple[CoverageItem, ...]
    warnings: tuple[str, ...]
    coverage_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class CandidateProposal(_FrozenStrict):
    proposal_id: Annotated[str, Field(min_length=1)]
    semantic_unit_id: Annotated[str, Field(min_length=1)]
    message_uuid: Annotated[str, Field(min_length=1)]
    text_unit_uuid: Annotated[str, Field(min_length=1)]
    raw_start: Annotated[int, Field(ge=0)]
    raw_end: Annotated[int, Field(gt=0)]
    evidence: Annotated[str, Field(min_length=1)]
    candidate_type: str
    subject_key: str
    predicate: str
    object_payload: dict[str, Any]
    normalized_text: str
    polarity: Literal["affirmed", "negated", "unknown"]
    epistemic_status: Literal["asserted", "hedged", "hypothetical", "questioned", "unknown"]
    durability: Literal["durable", "transient", "context_only", "unknown"]
    source_authority: str
    explicitness: str
    privacy_ceiling: Literal["normal", "sensitive", "secret"]
    status: str
    gate_decision_uuid: Annotated[str, Field(min_length=1)]
    route_uuid: Annotated[str, Field(min_length=1)]
    annotation_uuid: Annotated[str, Field(min_length=1)]
    prompt_execution_uuid: Annotated[str, Field(min_length=1)]
    context_lineage: tuple[dict[str, Any], ...]
    reason_codes: tuple[str, ...]
    automatic_candidate_creation_allowed: Literal[True]
    semantic_unit_fingerprint: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
