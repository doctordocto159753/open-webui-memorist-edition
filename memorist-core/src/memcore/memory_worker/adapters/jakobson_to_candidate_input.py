from __future__ import annotations

from typing import Any

from memcore.models import (
    CandidateEvidence,
    EvidenceRole,
    JakobsonSentenceAnnotation,
    MemorySignalRoute,
    Message,
    SupportType,
    TextUnit,
)


def build_candidate_extraction_input(
    *,
    annotation: JakobsonSentenceAnnotation,
    route: MemorySignalRoute,
    text_unit: TextUnit,
    message: Message,
    session_context: dict[str, Any] | None = None,
    project_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "message_uuid": message.message_uuid,
        "session_uuid": message.session_uuid,
        "unit_uuid": text_unit.text_unit_uuid,
        "annotation_uuid": annotation.annotation_uuid,
        "route_uuid": route.route_uuid,
        "route_type": route.route_type.value,
        "extractor_id": route.extractor_id,
        "dominant_function": annotation.dominant_function.value,
        "secondary_functions_ijson": annotation.secondary_functions_ijson,
        "sentence": {
            "text": annotation.sentence_text,
            "char_start": text_unit.start_char,
            "char_end": text_unit.end_char,
            "content_hash": text_unit.content_hash,
        },
        "six_factors": {
            "sender_addresser": annotation.sender_value,
            "receiver_addressee": annotation.receiver_value,
            "message": annotation.message_value,
            "context_referent": annotation.context_value,
            "code": annotation.code_value,
            "contact_channel": annotation.contact_channel_value,
        },
        "evidence": {
            "message_uuid": message.message_uuid,
            "unit_uuid": text_unit.text_unit_uuid,
            "annotation_uuid": annotation.annotation_uuid,
            "route_uuid": route.route_uuid,
            "quote": text_unit.text,
            "char_start": text_unit.start_char,
            "char_end": text_unit.end_char,
        },
        "session_context": session_context or {},
        "project_context": project_context or {},
    }


def candidate_evidence_from_route(
    *,
    candidate_uuid: str,
    message: Message,
    text_unit: TextUnit,
    annotation: JakobsonSentenceAnnotation,
    route: MemorySignalRoute,
) -> CandidateEvidence:
    return CandidateEvidence(
        candidate_uuid=candidate_uuid,
        message_uuid=message.message_uuid,
        text_unit_uuid=text_unit.text_unit_uuid,
        annotation_uuid=annotation.annotation_uuid,
        route_uuid=route.route_uuid,
        evidence_text=text_unit.text,
        start_char=text_unit.start_char,
        end_char=text_unit.end_char,
        evidence_role=EvidenceRole.PRIMARY,
        support_type=SupportType.SUPPORTING,
    )
