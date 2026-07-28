from __future__ import annotations

from typing import Any

from memcore.memory_worker.extraction.schemas import ExtractedCandidate
from memcore.memory_worker.semantic.authority import CandidateAuthorityContext
from memcore.memory_worker.semantic.candidate_service import (
    CandidateServiceInput,
    LinguisticCandidateComplements,
    build_candidate_draft,
    read_modality_polarity,
)
from memcore.models import (
    CandidateEvidence,
    EvidenceRole,
    LinguisticAnalysis,
    MemoryCandidate,
    Message,
    SupportType,
    TextUnit,
)
from memcore.validators.ijson import dump_ijson, load_ijson


class CandidateExtractor:
    """Adapt the shared canonical candidate draft to Lite persistence models."""

    def extract(
        self,
        message: Message,
        unit: TextUnit,
        processing_run_uuid: str,
        analysis: LinguisticAnalysis | None,
        *,
        authority: CandidateAuthorityContext,
        imported_record: bool = False,
        provider_type: str | None = None,
        model_name: str | None = None,
    ) -> list[ExtractedCandidate]:
        draft = build_candidate_draft(
            CandidateServiceInput(
                message_uuid=message.message_uuid,
                message_role=message.role.value,
                text_unit_uuid=unit.text_unit_uuid,
                text=unit.text,
                start_char=unit.start_char,
                end_char=unit.end_char,
                processing_run_uuid=processing_run_uuid,
                authority=authority,
                imported_record=imported_record,
                provider_type=provider_type,
                model_name=model_name,
                complements=_linguistic_complements(analysis),
            )
        )
        if draft is None:
            return []

        candidate = MemoryCandidate(
            processing_run_uuid=draft.processing_run_uuid,
            text_unit_uuid=draft.text_unit_uuid,
            prompt_execution_uuid=draft.prompt_execution_uuid,
            candidate_type=draft.candidate_type,
            subject_key=draft.subject_key,
            predicate=draft.predicate,
            object_ijson=dump_ijson(draft.object_payload),
            normalized_text=draft.normalized_text,
            source_authority=draft.source_authority,
            explicitness=draft.explicitness,
            confidence=draft.confidence,
            importance=draft.importance,
            polarity=draft.polarity,
            valid_from=draft.valid_from,
            valid_until=None,
            temporal_precision=draft.temporal_precision,
            status=draft.status,
            sensitivity_class=draft.sensitivity,
            extraction_metadata_ijson=dump_ijson(draft.metadata),
            rejection_reason_codes_ijson=(
                dump_ijson(list(draft.rejection_reason_codes))
                if draft.rejection_reason_codes
                else None
            ),
        )
        evidence = CandidateEvidence(
            candidate_uuid=candidate.candidate_uuid,
            message_uuid=draft.message_uuid,
            text_unit_uuid=draft.text_unit_uuid,
            annotation_uuid=draft.annotation_uuid,
            route_uuid=draft.route_uuid,
            evidence_text=draft.evidence_text,
            start_char=draft.start_char,
            end_char=draft.end_char,
            evidence_role=EvidenceRole.PRIMARY,
            support_type=SupportType.SUPPORTING,
        )
        return [ExtractedCandidate(candidate=candidate, evidence=[evidence])]


def _linguistic_complements(
    analysis: LinguisticAnalysis | None,
) -> LinguisticCandidateComplements:
    if analysis is None:
        return LinguisticCandidateComplements(abstained=True)
    modality = _mapping(load_ijson(analysis.modality_ijson))
    temporal = load_ijson(analysis.temporal_expressions_ijson)
    first_temporal = temporal[0] if isinstance(temporal, list) and temporal else {}
    abstention = _mapping(load_ijson(analysis.abstention_ijson))
    return LinguisticCandidateComplements(
        analysis_uuid=analysis.analysis_uuid,
        polarity=read_modality_polarity(modality),
        valid_from=_optional_string(_mapping(first_temporal).get("normalized")),
        temporal_precision=_optional_string(_mapping(first_temporal).get("precision")),
        abstained=bool(abstention.get("abstained")),
    )


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _optional_string(value: Any) -> str | None:
    return None if value is None else str(value)
