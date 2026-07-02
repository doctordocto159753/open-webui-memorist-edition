from memcore.models import Explicitness, SourceAuthority


def derive_confidence(
    source_authority: SourceAuthority,
    explicitness: Explicitness,
    evidence_complete: bool,
    temporal_clear: bool = True,
    negated: bool = False,
) -> float:
    score = 0.45
    if source_authority is SourceAuthority.USER_EXPLICIT:
        score += 0.25
    elif source_authority is SourceAuthority.TOOL_OBSERVATION:
        score += 0.2
    elif source_authority is SourceAuthority.ASSISTANT_CLAIM:
        score -= 0.2
    if explicitness is Explicitness.EXPLICIT:
        score += 0.15
    if evidence_complete:
        score += 0.1
    if temporal_clear:
        score += 0.03
    if negated:
        score -= 0.05
    return max(0.0, min(1.0, round(score, 3)))
