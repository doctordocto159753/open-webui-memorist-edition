"""Shared modality payload shape for Lite and Full.

The deterministic analyzer may emit lexical hints for diagnostics and bounded
repair, but it is not semantic authority. Canonical polarity, polarity scope,
and epistemic status belong to a validated model semantic unit. Until that
validated unit exists, candidate persistence must read polarity as ``unknown``.
"""

from __future__ import annotations

from typing import Any

from memcore.textsemantics import extract_polarity, is_hypothetical, normalize_with_mapping

NON_AUTHORITATIVE_LEXICAL_HINT = "non_authoritative"
VALIDATED_MODEL_ANALYSIS = "validated_model"


def modality_payload(text: str) -> dict[str, Any]:
    """Return non-authoritative lexical hints in the legacy payload shape.

    ``polarity`` and ``hypothetical`` remain present for diagnostics and bounded
    repair compatibility. ``semantic_authority`` is the hard boundary: readers
    that create candidates or memories must ignore these values unless the
    payload is explicitly stamped ``validated_model`` by the model-analysis
    contract introduced in WP02.
    """

    normalized = normalize_with_mapping(text)
    polarity = extract_polarity(normalized)
    return {
        "polarity": polarity.polarity.value,
        "negated": polarity.negated,
        "polarity_evidence": polarity.evidence,
        "hypothetical": is_hypothetical(normalized),
        "semantic_authority": NON_AUTHORITATIVE_LEXICAL_HINT,
    }
