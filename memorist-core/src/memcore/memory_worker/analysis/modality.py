"""The single modality payload shared by both runtimes.

Lite builds this from its structured analyzer and Full from its pipeline. They
must produce the same shape from the same text, or the two runtimes disagree
about whether a stored claim is asserted or denied.
"""

from __future__ import annotations

from typing import Any

from memcore.textsemantics import extract_polarity, is_hypothetical, normalize_with_mapping


def modality_payload(text: str) -> dict[str, Any]:
    """Derive modality for one text unit.

    ``negated`` is kept alongside ``polarity`` so readers written before
    polarity existed keep working unchanged.
    """

    normalized = normalize_with_mapping(text)
    polarity = extract_polarity(normalized)
    return {
        "polarity": polarity.polarity.value,
        "negated": polarity.negated,
        "polarity_evidence": polarity.evidence,
        "hypothetical": is_hypothetical(normalized),
    }
