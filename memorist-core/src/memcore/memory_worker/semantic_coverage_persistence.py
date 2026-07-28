"""Common persistence contract and deterministic replay identities for WP02."""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from memcore.memory_worker.semantic.coverage import CandidateProposal, CoveragePlan
from memcore.models import CandidateEvidence, MemoryCandidate
from memcore.validators.ijson import canonical_hash_ijson

COVERAGE_RUN_NAMESPACE = uuid.UUID("16617151-5e87-54b0-b538-376911bb1959")
EVIDENCE_NAMESPACE = uuid.UUID("478e7c02-f908-55ef-b60e-a37b8b82ae12")


class SemanticCoverageIdentityConflict(RuntimeError):
    """A deterministic identity was replayed with different canonical content."""


@dataclass(frozen=True)
class CoveragePersistenceBindings:
    message_version_uuid: str | None
    text_envelope_contract_version: str
    semantic_unit_fingerprints: Mapping[str, str]
    annotation_uuids: Mapping[str, str | None]


@dataclass(frozen=True)
class CandidateAuthorityBinding:
    """Persisted authority that must still hold inside the final transaction."""

    processing_run_uuid: str
    text_unit_uuid: str
    gate_decision_uuid: str
    gate_decision: str
    annotation_uuid: str
    route_uuid: str
    route_type: str
    route_status: str


class SemanticCoveragePersistence(Protocol):
    def persist_plan(
        self, plan: CoveragePlan, bindings: CoveragePersistenceBindings
    ) -> dict[str, Any]: ...

    def reserve_candidate(
        self, proposal_id: str, coverage_item_id: str, payload_hash: str
    ) -> dict[str, Any]: ...

    def create_and_link_candidate(
        self,
        proposal: CandidateProposal,
        candidate: MemoryCandidate,
        evidence_items: Sequence[CandidateEvidence],
        authority: CandidateAuthorityBinding | None = None,
    ) -> dict[str, Any]: ...


def coverage_run_uuid(coverage_hash: str) -> str:
    return str(uuid.uuid5(COVERAGE_RUN_NAMESPACE, coverage_hash))


def candidate_payload_hash(
    candidate: MemoryCandidate, evidence_items: Sequence[CandidateEvidence]
) -> str:
    """Hash candidate/evidence semantics, excluding timestamps and random IDs."""

    return canonical_hash_ijson(
        {
            "candidate_uuid": candidate.candidate_uuid,
            "processing_run_uuid": candidate.processing_run_uuid,
            "text_unit_uuid": candidate.text_unit_uuid,
            "prompt_execution_uuid": candidate.prompt_execution_uuid,
            "candidate_type": candidate.candidate_type.value,
            "subject_key": candidate.subject_key,
            "predicate": candidate.predicate,
            "object_ijson": candidate.object_ijson,
            "normalized_text": candidate.normalized_text,
            "source_authority": candidate.source_authority.value,
            "explicitness": candidate.explicitness.value,
            "confidence": candidate.confidence,
            "polarity": candidate.polarity.value,
            "importance": candidate.importance,
            "valid_from_value": candidate.valid_from,
            "valid_until_value": candidate.valid_until,
            "temporal_precision": candidate.temporal_precision,
            "status": candidate.status.value,
            "sensitivity_class": candidate.sensitivity_class.value,
            "extraction_metadata_ijson": candidate.extraction_metadata_ijson,
            "rejection_reason_codes_ijson": candidate.rejection_reason_codes_ijson,
            "evidence": [
                {
                    "message_uuid": item.message_uuid,
                    "text_unit_uuid": item.text_unit_uuid,
                    "annotation_uuid": item.annotation_uuid,
                    "route_uuid": item.route_uuid,
                    "evidence_text": item.evidence_text,
                    "start_char": item.start_char,
                    "end_char": item.end_char,
                    "evidence_role": item.evidence_role.value,
                    "support_type": item.support_type.value,
                }
                for item in evidence_items
            ],
        }
    )


def deterministic_evidence(proposal_id: str, evidence: CandidateEvidence) -> CandidateEvidence:
    identity = canonical_hash_ijson(
        {
            "proposal_id": proposal_id,
            "message_uuid": evidence.message_uuid,
            "text_unit_uuid": evidence.text_unit_uuid,
            "annotation_uuid": evidence.annotation_uuid,
            "route_uuid": evidence.route_uuid,
            "start_char": evidence.start_char,
            "end_char": evidence.end_char,
            "evidence_role": evidence.evidence_role.value,
            "support_type": evidence.support_type.value,
        }
    )
    return evidence.model_copy(
        update={
            "evidence_uuid": str(uuid.uuid5(EVIDENCE_NAMESPACE, identity)),
            "candidate_uuid": proposal_id,
        }
    )


def validate_candidate_binding(
    proposal: CandidateProposal,
    candidate: MemoryCandidate,
    evidence_items: Sequence[CandidateEvidence],
) -> tuple[CandidateEvidence, ...]:
    if candidate.candidate_uuid != proposal.proposal_id:
        raise SemanticCoverageIdentityConflict(
            "candidate UUID must equal deterministic proposal UUID"
        )
    if not evidence_items:
        raise ValueError("candidate link requires at least one evidence span")
    deterministic = tuple(
        deterministic_evidence(proposal.proposal_id, evidence) for evidence in evidence_items
    )
    for evidence in deterministic:
        if (
            evidence.message_uuid != proposal.message_uuid
            or evidence.text_unit_uuid != proposal.text_unit_uuid
            or evidence.start_char != proposal.raw_start
            or evidence.end_char != proposal.raw_end
            or evidence.evidence_text != proposal.evidence
        ):
            raise SemanticCoverageIdentityConflict(
                "candidate evidence does not match its deterministic proposal"
            )
    return deterministic
