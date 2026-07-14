from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from memcore.memory_worker.extraction.sensitivity import classify_sensitivity
from memcore.memory_worker.semantic.authority import CandidateAuthorityContext
from memcore.memory_worker.semantic.candidate_mapping import (
    ROUTE_CANDIDATE_MAPPING_VERSION,
    candidate_mapping_for_route,
)
from memcore.memory_worker.semantic.gate_policy import (
    candidate_policy_for_gate_and_route,
)
from memcore.memory_worker.semantic.provenance_policy import (
    PROVENANCE_POLICY_VERSION,
    decide_candidate_provenance,
)
from memcore.models import (
    CandidateStatus,
    CandidateType,
    Explicitness,
    SensitivityClass,
    SourceAuthority,
)


@dataclass(frozen=True)
class LinguisticCandidateComplements:
    analysis_uuid: str | None = None
    negated: bool = False
    valid_from: str | None = None
    temporal_precision: str | None = None
    abstained: bool = False


@dataclass(frozen=True)
class CandidateServiceInput:
    message_uuid: str
    message_role: str
    text_unit_uuid: str
    text: str
    start_char: int
    end_char: int
    processing_run_uuid: str
    authority: CandidateAuthorityContext
    imported_record: bool = False
    provider_type: str | None = None
    model_name: str | None = None
    complements: LinguisticCandidateComplements = LinguisticCandidateComplements()


@dataclass(frozen=True)
class CandidateDraft:
    processing_run_uuid: str
    text_unit_uuid: str
    prompt_execution_uuid: str | None
    candidate_type: CandidateType
    subject_key: str
    predicate: str
    object_payload: dict[str, Any]
    normalized_text: str
    source_authority: SourceAuthority
    explicitness: Explicitness
    confidence: float
    importance: float
    valid_from: str | None
    temporal_precision: str | None
    status: CandidateStatus
    sensitivity: SensitivityClass
    rejection_reason_codes: tuple[str, ...]
    metadata: dict[str, Any]
    message_uuid: str
    annotation_uuid: str
    route_uuid: str
    evidence_text: str
    start_char: int
    end_char: int
    allows_automatic_memory_creation: bool


def build_candidate_draft(value: CandidateServiceInput) -> CandidateDraft | None:
    """Construct the runtime-neutral candidate persisted by Lite and Full."""

    route = value.authority.selected_route
    policy = candidate_policy_for_gate_and_route(
        gate_decision=value.authority.gate_decision,
        route_type=route.route_type if route is not None else None,
        route_status=route.route_status if route is not None else None,
        requires_high_confidence_pass=value.authority.requires_high_confidence_pass,
    )
    if not policy.allows_candidate_creation or route is None:
        return None

    mapping = candidate_mapping_for_route(
        route.route_type,
        value.text,
        message_uuid=value.message_uuid,
    )
    if mapping is None:
        return None
    provenance = decide_candidate_provenance(
        message_role=value.message_role,
        route_type=route.route_type,
        route_status=mapping.status,
        imported_record=value.imported_record,
    )
    if not provenance.allows_candidate_creation:
        return None

    sensitivity = classify_sensitivity(value.text)
    status = provenance.status
    reasons = [*mapping.rejection_reason_codes, *provenance.reason_codes]
    if sensitivity is SensitivityClass.SECRET:
        status = CandidateStatus.REJECTED
        reasons.append("secret_detected")
    elif sensitivity is SensitivityClass.SENSITIVE and status is not CandidateStatus.REJECTED:
        status = CandidateStatus.NEEDS_REVIEW
        reasons.append("sensitive_requires_review")

    allows_memory = (
        policy.allows_automatic_memory_creation
        and provenance.allows_automatic_memory_creation
        and status is CandidateStatus.READY_FOR_CONSOLIDATION
    )
    confidence = _confidence(
        provenance.source_authority,
        provenance.explicitness,
        negated=value.complements.negated,
        status=status,
    )
    metadata: dict[str, Any] = {
        "semantic_authority": "jakobson",
        "source_authority": provenance.source_authority.value,
        "explicitness": provenance.explicitness.value,
        "route_mapping_version": ROUTE_CANDIDATE_MAPPING_VERSION,
        "provenance_policy_version": PROVENANCE_POLICY_VERSION,
        "gate_decision": policy.gate_decision,
        "gate_reason_codes": list(policy.reason_codes),
        "route_type": route.route_type.value,
        "route_status": route.route_status.value,
        "annotation_uuid": route.annotation_uuid,
        "route_uuid": route.route_uuid,
        "analysis_run_uuid": value.authority.analysis_run_uuid,
        "prompt_execution_uuid": value.authority.prompt_execution_uuid,
        "linguistic_analysis_uuid": value.complements.analysis_uuid,
        "linguistic_analysis_abstained": value.complements.abstained,
        "requires_high_confidence_pass": policy.requires_high_confidence_pass,
        "allows_automatic_memory_creation": allows_memory,
        "provider_type": value.provider_type,
        "model_name": value.model_name,
    }
    return CandidateDraft(
        processing_run_uuid=value.processing_run_uuid,
        text_unit_uuid=value.text_unit_uuid,
        prompt_execution_uuid=value.authority.prompt_execution_uuid,
        candidate_type=mapping.candidate_type,
        subject_key=mapping.subject_key,
        predicate=mapping.predicate,
        object_payload={"value": mapping.object_value},
        normalized_text=mapping.normalized_text,
        source_authority=provenance.source_authority,
        explicitness=provenance.explicitness,
        confidence=confidence,
        importance=mapping.importance,
        valid_from=value.complements.valid_from,
        temporal_precision=value.complements.temporal_precision,
        status=status,
        sensitivity=sensitivity,
        rejection_reason_codes=tuple(dict.fromkeys(reasons)),
        metadata=metadata,
        message_uuid=value.message_uuid,
        annotation_uuid=route.annotation_uuid,
        route_uuid=route.route_uuid,
        evidence_text=value.text.strip(),
        start_char=value.start_char + len(value.text) - len(value.text.lstrip()),
        end_char=value.end_char - (len(value.text) - len(value.text.rstrip())),
        allows_automatic_memory_creation=allows_memory,
    )


def _confidence(
    source_authority: SourceAuthority,
    explicitness: Explicitness,
    *,
    negated: bool,
    status: CandidateStatus,
) -> float:
    score = 0.45
    if source_authority is SourceAuthority.USER_EXPLICIT:
        score += 0.25
    elif source_authority is SourceAuthority.TOOL_OBSERVATION:
        score += 0.20
    elif source_authority is SourceAuthority.SYSTEM_INSTRUCTION:
        score += 0.15
    elif source_authority is SourceAuthority.IMPORTED_RECORD:
        score += 0.10
    elif source_authority is SourceAuthority.ASSISTANT_CLAIM:
        score -= 0.20
    if explicitness is Explicitness.EXPLICIT:
        score += 0.15
    score += 0.13
    if negated:
        score -= 0.05
    if status is CandidateStatus.NEEDS_REVIEW:
        score = min(score, 0.65)
    elif status is CandidateStatus.REJECTED:
        score = min(score, 0.35)
    return max(0.0, min(1.0, round(score, 3)))
