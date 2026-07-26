from memcore.models import Explicitness, SourceAuthority


def derive_confidence(
    source_authority: SourceAuthority,
    explicitness: Explicitness,
    evidence_complete: bool,
    temporal_clear: bool = True,
) -> float:
    """Confidence that the claim was extracted correctly.

    Polarity is deliberately not an input. "We never deploy on Friday" is
    asserted exactly as certainly as "we deploy on Friday", so a negated claim
    must not score lower for being negated; negation is carried by the
    candidate's polarity field instead.

    Every other coefficient here is unchanged from the pre-PRD-02 formula.
    Broader recalibration of these weights remains deferred.
    """

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
    return max(0.0, min(1.0, round(score, 3)))
