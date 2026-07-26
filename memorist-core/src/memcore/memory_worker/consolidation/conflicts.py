from memcore.textsemantics import Polarity, extract_polarity


def appears_contradictory(existing_text: str, new_text: str) -> bool:
    """Whether two claims assert opposite polarity.

    Detection runs through the shared polarity extractor, which matches on
    token boundaries. The previous substring markers fired on "not " inside
    "cannot ship" and "never " inside "nevertheless", turning ordinary updates
    into contradictions.

    Only a decided disagreement counts. When either side is ``unknown`` there
    is nothing to contradict and the caller supersedes instead of raising a
    conflict it cannot substantiate.
    """

    existing = extract_polarity(existing_text).polarity
    new = extract_polarity(new_text).polarity
    if Polarity.UNKNOWN in {existing, new}:
        return False
    return existing is not new
