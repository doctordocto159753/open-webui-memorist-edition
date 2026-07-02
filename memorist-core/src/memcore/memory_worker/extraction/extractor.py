import re
from typing import Any

from memcore.memory_worker import reason_codes as reasons
from memcore.memory_worker.extraction.confidence import derive_confidence
from memcore.memory_worker.extraction.evidence import primary_evidence
from memcore.memory_worker.extraction.schemas import ExtractedCandidate
from memcore.memory_worker.extraction.sensitivity import classify_sensitivity
from memcore.models import (
    CandidateStatus,
    CandidateType,
    Explicitness,
    LinguisticAnalysis,
    MemoryCandidate,
    Message,
    SourceAuthority,
    TextUnit,
)
from memcore.validators.ijson import dump_ijson, load_ijson


class CandidateExtractor:
    def extract(
        self,
        message: Message,
        unit: TextUnit,
        processing_run_uuid: str,
        analysis: LinguisticAnalysis,
    ) -> list[ExtractedCandidate]:
        text = unit.text.strip()
        if not text:
            return []

        candidate_type, subject_key, predicate, obj = _classify_candidate(text)
        sensitivity = classify_sensitivity(text)
        source_authority = _source_authority(message.role.value)
        explicitness = Explicitness.EXPLICIT
        reason_codes = _rejection_reasons(text, source_authority, sensitivity)
        status = _status_from_reasons(reason_codes)
        if candidate_type is CandidateType.UNRESOLVED_QUESTION and not reason_codes:
            reason_codes.append(reasons.QUESTION_NOT_ASSERTION)
            status = CandidateStatus.REJECTED

        if candidate_type is None:
            return []

        modality = load_ijson(analysis.modality_ijson)
        temporal = load_ijson(analysis.temporal_expressions_ijson)
        temporal_clear = not temporal or bool(temporal[0].get("certain"))
        confidence = derive_confidence(
            source_authority,
            explicitness,
            evidence_complete=True,
            temporal_clear=temporal_clear,
            negated=bool(modality.get("negated")),
        )
        if status is CandidateStatus.NEEDS_REVIEW:
            confidence = min(confidence, 0.65)
        if status is CandidateStatus.REJECTED:
            confidence = min(confidence, 0.35)

        candidate = MemoryCandidate(
            processing_run_uuid=processing_run_uuid,
            text_unit_uuid=unit.text_unit_uuid,
            candidate_type=candidate_type,
            subject_key=subject_key,
            predicate=predicate,
            object_ijson=dump_ijson({"value": obj}) if obj is not None else None,
            normalized_text=_normalized_text(candidate_type, subject_key, predicate, obj, text),
            source_authority=source_authority,
            explicitness=explicitness,
            confidence=confidence,
            importance=_importance(candidate_type, text),
            valid_from=_valid_from(temporal),
            valid_until=None,
            temporal_precision=_temporal_precision(temporal),
            status=status,
            sensitivity_class=sensitivity,
            extraction_metadata_ijson=dump_ijson(
                {
                    "extractor_version": "deterministic-extractor-v1",
                    "analysis_uuid": analysis.analysis_uuid,
                }
            ),
            rejection_reason_codes_ijson=dump_ijson(reason_codes) if reason_codes else None,
        )
        return [
            ExtractedCandidate(
                candidate=candidate,
                evidence=[primary_evidence(candidate.candidate_uuid, message.message_uuid, unit)],
            )
        ]


def _classify_candidate(
    text: str,
) -> tuple[CandidateType | None, str | None, str | None, Any]:
    lowered = text.lower()
    if "?" in text or "؟" in text:
        return CandidateType.UNRESOLVED_QUESTION, "user", "question", text
    if "prefer" in lowered or "ترجیح" in text:
        value = _after_markers(text, ("prefer", "ترجیح می‌دهم", "ترجیح می دهم")) or text
        return CandidateType.PREFERENCE, "user", "preference", value.strip(" .")
    if "decided" in lowered or "تصمیم گرفتیم" in text:
        return CandidateType.DECISION, "project", "decision", text.strip(" .")
    if "do not" in lowered or "don't" in lowered or "نباید" in text or "هرگز" in text:
        return CandidateType.CONSTRAINT, "project", "constraint", text.strip(" .")
    if "i mean" in lowered or "منظورم از" in text:
        subject = _definition_subject(text)
        return CandidateType.DEFINITION, subject, "meaning", text.strip(" .")
    if "my name is" in lowered or "نام من" in text:
        return CandidateType.IDENTITY, "user", "name", text.strip(" .")
    if "forget this" in lowered or "فراموش کن" in text:
        return CandidateType.INSTRUCTION, "privacy", "forget_request", text.strip(" .")
    if "currently" in lowered or "فعلا" in text or "فعلاً" in text:
        return CandidateType.EPISODE, "session", "temporary_state", text.strip(" .")
    if "remember this" in lowered or "یادت" in text:
        return CandidateType.FACT, "user", "remembered_fact", text.strip(" .")
    return None, None, None, None


def _source_authority(role: str) -> SourceAuthority:
    if role == "assistant":
        return SourceAuthority.ASSISTANT_CLAIM
    if role == "tool":
        return SourceAuthority.TOOL_OBSERVATION
    if role == "system":
        return SourceAuthority.SYSTEM_INSTRUCTION
    return SourceAuthority.USER_EXPLICIT


def _rejection_reasons(
    text: str,
    source_authority: SourceAuthority,
    sensitivity: object,
) -> list[str]:
    lowered = text.lower()
    reasons_found: list[str] = []
    if "api_key" in lowered or "api key" in lowered or "password" in lowered or "token" in lowered:
        reasons_found.append(reasons.SECRET_DETECTED)
    if source_authority is SourceAuthority.ASSISTANT_CLAIM and "decided" not in lowered:
        reasons_found.append(reasons.ASSISTANT_AS_USER_FACT)
    if re.search(r"\bif\b|hypothetically", lowered):
        reasons_found.append(reasons.HYPOTHETICAL)
    if '"' in text and any(term in lowered for term in ("religion", "medical", "political")):
        reasons_found.append(reasons.QUOTED_THIRD_PARTY)
    if str(sensitivity) == "sensitive":
        reasons_found.append(reasons.SENSITIVE_REQUIRES_REVIEW)
    if "forget this" in lowered or "فراموش کن" in text:
        reasons_found.append(reasons.SENSITIVE_REQUIRES_REVIEW)
    return reasons_found


def _status_from_reasons(reason_codes: list[str]) -> CandidateStatus:
    if not reason_codes:
        return CandidateStatus.READY_FOR_CONSOLIDATION
    if reasons.SENSITIVE_REQUIRES_REVIEW in reason_codes:
        return CandidateStatus.NEEDS_REVIEW
    return CandidateStatus.REJECTED


def _normalized_text(
    candidate_type: CandidateType,
    subject_key: str | None,
    predicate: str | None,
    obj: Any,
    fallback: str,
) -> str:
    if subject_key and predicate and obj is not None:
        return f"{candidate_type.value}:{subject_key}:{predicate}:{obj}".strip()
    return fallback.strip()


def _importance(candidate_type: CandidateType, text: str) -> float:
    if candidate_type in {CandidateType.CONSTRAINT, CandidateType.DECISION}:
        return 0.85
    if candidate_type in {CandidateType.PREFERENCE, CandidateType.DEFINITION}:
        return 0.7
    if "always" in text.lower() or "never" in text.lower():
        return 0.8
    return 0.5


def _after_markers(text: str, markers: tuple[str, ...]) -> str | None:
    lowered = text.lower()
    for marker in markers:
        index = lowered.find(marker.lower())
        if index >= 0:
            return text[index + len(marker) :]
    return None


def _definition_subject(text: str) -> str:
    lowered = text.lower()
    if lowered.startswith("by ") and " i mean " in lowered:
        return text[3 : lowered.find(" i mean ")].strip()
    if "منظورم از" in text:
        return text.split("منظورم از", 1)[1].split("این", 1)[0].strip() or "term"
    return "term"


def _valid_from(temporal: Any) -> str | None:
    if isinstance(temporal, list) and temporal:
        normalized = temporal[0].get("normalized")
        return str(normalized) if normalized else None
    return None


def _temporal_precision(temporal: Any) -> str | None:
    if isinstance(temporal, list) and temporal:
        precision = temporal[0].get("precision")
        return str(precision) if precision else None
    return None
