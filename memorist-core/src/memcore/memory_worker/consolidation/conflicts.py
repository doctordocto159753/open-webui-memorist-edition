from __future__ import annotations

from memcore.textsemantics import Polarity, coerce_polarity, extract_polarity


def polarities_contradict(
    existing_polarity: Polarity | str,
    new_polarity: Polarity | str,
) -> bool:
    """Whether two already-decided claim polarities disagree.

    Production consolidation must call this function with persisted candidate
    and memory-version polarity. It never infers meaning from text.
    """

    existing = coerce_polarity(existing_polarity)
    new = coerce_polarity(new_polarity)
    if Polarity.UNKNOWN in {existing, new}:
        return False
    return existing is not new


def appears_contradictory(existing_text: str, new_text: str) -> bool:
    """Non-authoritative lexical diagnostic retained for compatibility.

    This helper is deliberately not used by production consolidation. It exists
    for tests, diagnostics, and bounded repair hints written before polarity
    became first-class. Canonical conflict decisions use
    :func:`polarities_contradict` with stored model-owned polarity.
    """

    return polarities_contradict(
        extract_polarity(existing_text).polarity,
        extract_polarity(new_text).polarity,
    )
