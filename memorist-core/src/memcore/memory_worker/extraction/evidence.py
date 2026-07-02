from memcore.models import CandidateEvidence, EvidenceRole, SupportType, TextUnit


def primary_evidence(
    candidate_uuid: str,
    message_uuid: str,
    unit: TextUnit,
) -> CandidateEvidence:
    evidence_text = unit.text.strip()
    local_start = unit.text.find(evidence_text)
    start_char = unit.start_char + local_start
    return CandidateEvidence(
        candidate_uuid=candidate_uuid,
        message_uuid=message_uuid,
        text_unit_uuid=unit.text_unit_uuid,
        evidence_text=evidence_text,
        start_char=start_char,
        end_char=start_char + len(evidence_text),
        evidence_role=EvidenceRole.PRIMARY,
        support_type=SupportType.SUPPORTING,
    )
