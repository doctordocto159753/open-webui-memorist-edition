from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from memcore.memory_worker.analysis.modality import (
    NON_AUTHORITATIVE_LEXICAL_HINT,
    VALIDATED_MODEL_ANALYSIS,
)
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
from memcore.textsemantics import (
    NORMALIZATION_CONTRACT_VERSION,
    TEXT_SEMANTICS_CONTRACT_VERSION,
    Polarity,
    coerce_polarity,
    polarity_from_flag,
)

CANDIDATE_SERVICE_VERSION = "pr4d-candidate-service-v1"


def read_modality_polarity(modality: dict[str, Any]) -> Polarity:
    """Read only authoritative or historical polarity decisions.

    New deterministic modality payloads are lexical hints stamped
    ``non_authoritative``. They are useful for diagnostics and bounded repair,
    but they must never become candidate or memory semantics. A future WP02
    model-analysis payload is accepted only when stamped ``validated_model``.

    Historical rows predate the authority stamp. Their recorded ``polarity`` or
    legacy ``negated`` value remains readable for compatibility and audit.
    Unknown authority labels fail closed to ``unknown``.
    """

    authority = modality.get("semantic_authority")
    if authority == NON_AUTHORITATIVE_LEXICAL_HINT:
        return Polarity.UNKNOWN
    if authority not in {None, VALIDATED_MODEL_ANALYSIS}:
        return Polarity.UNKNOWN
    if "polarity" in modality:
        return coerce_polarity(modality.get("polarity"))
    if "negated" in modality:
        return polarity_from_flag(bool(modality.get("negated")))
    return Polarity.UNKNOWN


@dataclass(frozen=True)
class LinguisticCandidateComplements:
    analysis_uuid: str | None = None
    polarity: Polarity = Polarity.UNKNOWN
    valid_from: str | None = None
    temporal_precision: str | None = None
    abstained: bool = False

    @property
    def negated(self) -> bool:
        """Boolean view of polarity for readers that still speak in flags."""

        return self.polarity is Polarity.NEGATED


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
    polarity: Polarity
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
        status=status,
    )
    metadata: dict[str, Any] = {
        "semantic_authority": "jakobson",
        "polarity": value.complements.polarity.value,
        "normalization_contract_version": NORMALIZATION_CONTRACT_VERSION,
        "text_semantics_contract_version": TEXT_SEMANTICS_CONTRACT_VERSION,
        "source_authority": provenance.source_authority.value,
        "explicitness": provenance.explicitness.value,
        "route_mapping_version": ROUTE_CANDIDATE_MAPPING_VERSION,
        "candidate_service_version": CANDIDATE_SERVICE_VERSION,
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
    if provenance.source_authority is SourceAuthority.ASSISTANT_CLAIM:
        metadata.update(
            {
                "artifact_kind": "assistant_authored_project_artifact",
                "authority_label": "earlier_assistant_produced_artifact",
                "not_user_fact": True,
            }
        )
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
        polarity=value.complements.polarity,
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
    status: CandidateStatus,
) -> float:
    """Confidence that the claim was extracted correctly.

    Polarity is deliberately not an input: a negated claim is asserted just as
    certainly as its positive equivalent, so the two must score the same when
    the extraction evidence is the same. Polarity travels on the candidate's
    own field. Every other coefficient is unchanged; broader recalibration of
    these weights remains deferred.
    """

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
    if status is CandidateStatus.NEEDS_REVIEW:
        score = min(score, 0.65)
    elif status is CandidateStatus.REJECTED:
        score = min(score, 0.35)
    return max(0.0, min(1.0, round(score, 3)))
