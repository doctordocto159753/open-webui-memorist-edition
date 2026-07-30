"""Frozen WP02 semantic prompt input and deterministic binding validation."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from memcore.memory_worker.extraction.sensitivity import classify_sensitivity
from memcore.memory_worker.prompts.contracts import SemanticAnalysisV1Output
from memcore.models import SensitivityClass
from memcore.textsemantics.result import TEXT_SEMANTICS_CONTRACT_VERSION, TextEnvelope
from memcore.textsemantics.validation import (
    EvidenceValidationReport,
    validate_semantic_evidence,
)

SEMANTIC_INPUT_CONTRACT_VERSION = "memorist.semantic_candidate_input.v1"


class _StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class BoundedContextItem(_StrictInput):
    context_item_id: Annotated[str, Field(min_length=1)]
    user_uuid: Annotated[str, Field(min_length=1)]
    session_uuid: Annotated[str, Field(min_length=1)]
    workspace_uuid: str | None
    project_uuid: str | None
    message_uuid: Annotated[str, Field(min_length=1)]
    message_version_uuid: str | None
    text_unit_uuid: Annotated[str, Field(min_length=1)]
    role: Literal["user", "assistant", "tool"]
    turn_index: Annotated[int, Field(ge=0)]
    unit_index: Annotated[int, Field(ge=0)]
    raw_start: Annotated[int, Field(ge=0)]
    raw_end: Annotated[int, Field(gt=0)]
    text: str
    raw_text_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    source_authority_ceiling: Literal["user_explicit", "assistant_claim", "tool_observation"]

    @model_validator(mode="after")
    def _integrity(self) -> BoundedContextItem:
        if self.raw_start >= self.raw_end:
            raise ValueError("raw_start must be less than raw_end")
        if hashlib.sha256(self.text.encode("utf-8")).hexdigest() != self.raw_text_hash:
            raise ValueError("raw_text_hash does not match text")
        expected = {
            "user": "user_explicit",
            "assistant": "assistant_claim",
            "tool": "tool_observation",
        }[self.role]
        if self.source_authority_ceiling != expected:
            raise ValueError("source_authority_ceiling exceeds the canonical role ceiling")
        return self


class SemanticContextBoundary(_StrictInput):
    user_uuid: Annotated[str, Field(min_length=1)]
    session_uuid: Annotated[str, Field(min_length=1)]
    workspace_uuid: str | None
    project_uuid: str | None
    baseline_limit: Literal[2]
    effective_limit: Literal[2, 6]
    dependency_expansion: bool

    @model_validator(mode="after")
    def _expansion_matches_limit(self) -> SemanticContextBoundary:
        if self.dependency_expansion != (self.effective_limit == 6):
            raise ValueError("dependency_expansion must match effective_limit")
        return self


class SemanticContractVersions(_StrictInput):
    semantic_input: Literal["memorist.semantic_candidate_input.v1"]
    semantic_prompt: Literal["1.0"]
    jakobson: Literal["3.0"]
    text_envelope: Literal["memorist.text.envelope.v3"]
    evidence_validator: Literal["memorist.text.semantic_evidence_validation.v1"]


class SemanticAnalysisV1Input(_StrictInput):
    current_message_uuid: Annotated[str, Field(min_length=1)]
    current_message_version_uuid: str | None
    current_raw_text: str
    text_envelope: dict[str, Any]
    bounded_context_items: list[BoundedContextItem]
    boundary: SemanticContextBoundary
    contract_versions: SemanticContractVersions

    @model_validator(mode="after")
    def _manifest_integrity(self) -> SemanticAnalysisV1Input:
        expected_hash = hashlib.sha256(self.current_raw_text.encode("utf-8")).hexdigest()
        if self.text_envelope.get("raw_text_hash") != expected_hash:
            raise ValueError("text_envelope raw_text_hash does not match current_raw_text")
        if self.text_envelope.get("contract_version") != TEXT_SEMANTICS_CONTRACT_VERSION:
            raise ValueError("text_envelope contract_version is not active")
        if len(self.bounded_context_items) > self.boundary.effective_limit:
            raise ValueError("bounded context exceeds effective_limit")

        seen: set[str] = set()
        prior_order: tuple[int, int] | None = None
        boundary = self.boundary
        for item in self.bounded_context_items:
            if item.context_item_id in seen:
                raise ValueError("bounded context item IDs must be unique")
            seen.add(item.context_item_id)
            order = (item.turn_index, item.unit_index)
            if prior_order is not None and order <= prior_order:
                raise ValueError("bounded context must be in canonical ascending order")
            prior_order = order
            if item.message_uuid == self.current_message_uuid:
                raise ValueError("current message must not appear in bounded context")
            if (
                item.user_uuid != boundary.user_uuid
                or item.session_uuid != boundary.session_uuid
                or item.workspace_uuid != boundary.workspace_uuid
                or item.project_uuid != boundary.project_uuid
            ):
                raise ValueError("bounded context crosses an authority boundary")
        return self


def build_semantic_input(
    *,
    current_message_uuid: str,
    current_message_version_uuid: str | None,
    current_raw_text: str,
    text_envelope: TextEnvelope,
    bounded_context_items: list[BoundedContextItem],
    boundary: SemanticContextBoundary,
) -> SemanticAnalysisV1Input:
    """Build and validate the only supported semantic prompt input shape."""

    return SemanticAnalysisV1Input(
        current_message_uuid=current_message_uuid,
        current_message_version_uuid=current_message_version_uuid,
        current_raw_text=current_raw_text,
        text_envelope=text_envelope.as_dict(),
        bounded_context_items=bounded_context_items,
        boundary=boundary,
        contract_versions=SemanticContractVersions(
            semantic_input="memorist.semantic_candidate_input.v1",
            semantic_prompt="1.0",
            jakobson="3.0",
            text_envelope="memorist.text.envelope.v3",
            evidence_validator="memorist.text.semantic_evidence_validation.v1",
        ),
    )


def validate_semantic_binding(
    input_payload: Mapping[str, Any],
    output: Mapping[str, Any],
) -> EvidenceValidationReport:
    """Validate semantic endpoints, ordering and WP01 evidence in one boundary."""

    try:
        semantic_input = SemanticAnalysisV1Input.model_validate(input_payload)
        semantic_output = SemanticAnalysisV1Output.model_validate(output)
    except ValidationError as error:
        raise ValueError(_first_issue(error)) from error

    units = semantic_output.semantic_units
    if semantic_output.status == "ok" and not units:
        raise ValueError("status=ok requires at least one semantic unit")
    if semantic_output.status == "abstain" and (
        units or semantic_output.references or semantic_output.relations
    ):
        raise ValueError("status=abstain requires empty semantic collections")

    unit_ids = {unit.id for unit in units}
    if len(unit_ids) != len(units):
        raise ValueError("semantic unit IDs must be unique")
    unit_spans = {unit.id: (unit.raw_start, unit.raw_end) for unit in units}
    ordered = [(unit.raw_start, unit.raw_end) for unit in units]
    if ordered != sorted(ordered):
        raise ValueError("semantic units must be ordered by raw span")
    raw_length = len(semantic_input.current_raw_text)
    if any(unit.raw_start >= unit.raw_end or unit.raw_end > raw_length for unit in units):
        raise ValueError("semantic unit span is outside the current message")
    sensitivity_rank = {
        SensitivityClass.NORMAL: 0,
        SensitivityClass.SENSITIVE: 1,
        SensitivityClass.SECRET: 2,
    }
    for unit in units:
        # Model-authored propositions may clarify meaning, but they may never
        # introduce more sensitive material than their exact source evidence.
        if (
            sensitivity_rank[classify_sensitivity(unit.proposition)]
            > sensitivity_rank[classify_sensitivity(unit.evidence)]
        ):
            raise ValueError("semantic proposition exceeds its evidence privacy ceiling")

    context_ids = {
        f"prior_context:{item.context_item_id}" for item in semantic_input.bounded_context_items
    }
    allowed_referents = {f"current_unit:{unit_id}" for unit_id in unit_ids} | context_ids
    reference_ids: set[str] = set()
    for reference in semantic_output.references:
        if reference.id in reference_ids:
            raise ValueError("semantic reference IDs must be unique")
        reference_ids.add(reference.id)
        if reference.source_unit_id not in unit_ids:
            raise ValueError("reference source is not a current semantic unit")
        source_start, source_end = unit_spans[reference.source_unit_id]
        if not (source_start <= reference.marker_start and reference.marker_end <= source_end):
            raise ValueError("reference marker must be contained in its source unit")
        if any(
            candidate not in allowed_referents for candidate in reference.candidate_referent_ids
        ):
            raise ValueError("reference candidate is outside the supplied manifest")
        if reference.status == "resolved":
            if (
                reference.selected_referent_id is None
                or reference.selected_referent_id not in reference.candidate_referent_ids
            ):
                raise ValueError("resolved reference must select one supplied candidate")
            if reference.selected_referent_id == f"current_unit:{reference.source_unit_id}":
                raise ValueError("resolved reference cannot target its own source unit")
        elif reference.selected_referent_id is not None:
            raise ValueError("ambiguous or unresolved reference cannot select a target")

    relation_ids: set[str] = set()
    for relation in semantic_output.relations:
        if relation.id in relation_ids:
            raise ValueError("semantic relation IDs must be unique")
        relation_ids.add(relation.id)
        if relation.source_unit_id not in unit_ids:
            raise ValueError("relation source is not a current semantic unit")
        source_start, source_end = unit_spans[relation.source_unit_id]
        if not (source_start <= relation.evidence_start and relation.evidence_end <= source_end):
            raise ValueError("relation evidence must be contained in its source unit")
        if relation.target_referent_id not in allowed_referents:
            raise ValueError("relation target is outside the supplied manifest")

    report = validate_semantic_evidence(
        semantic_input.current_raw_text,
        semantic_output.model_dump(mode="json"),
        allowed_referent_ids=allowed_referents,
    )
    if not report.ok:
        first = report.rejections[0]
        raise ValueError(f"{first.record_kind}: {first.violation.value}")
    return report


def semantic_validation_issues(
    input_payload: Mapping[str, Any], output: Mapping[str, Any]
) -> list[dict[str, str]]:
    try:
        validate_semantic_binding(input_payload, output)
    except (TypeError, ValueError) as error:
        return [{"path": "(semantic)", "code": "binding", "message": str(error)}]
    return []


def _first_issue(error: ValidationError) -> str:
    issue = error.errors(include_url=False)[0]
    path = ".".join(str(part) for part in issue.get("loc", ())) or "(root)"
    return f"{path}: {issue.get('msg', 'invalid value')}"
