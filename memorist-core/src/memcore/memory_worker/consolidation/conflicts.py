NEGATION_MARKERS = ("not ", "never ", "don't ", "do not ", "نباید", "هرگز")


def appears_contradictory(existing_text: str, new_text: str) -> bool:
    existing_negated = any(marker in existing_text.lower() for marker in NEGATION_MARKERS)
    new_negated = any(marker in new_text.lower() for marker in NEGATION_MARKERS)
    return existing_negated != new_negated
