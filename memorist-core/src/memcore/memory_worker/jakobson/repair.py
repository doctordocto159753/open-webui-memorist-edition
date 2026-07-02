from __future__ import annotations

import re


class SentenceMappingError(ValueError):
    """Raised when provider sentence text cannot be mapped to deterministic units."""


def compare_sentence_text(provider_text: str, unit_text: str) -> str | None:
    if provider_text == unit_text:
        return None
    if provider_text.strip() == unit_text.strip():
        return "trimmed whitespace mismatch"
    if _minor_punctuation_form(provider_text) == _minor_punctuation_form(unit_text):
        return "minor punctuation mismatch"
    raise SentenceMappingError("provider sentence text does not match deterministic unit")


def _minor_punctuation_form(text: str) -> str:
    normalized = re.sub(r"[\s\u200c]+", " ", text.strip())
    normalized = normalized.replace("؟", "?").replace("۔", ".").replace("؛", ";")
    return normalized.rstrip(" .!?;:،,").casefold()
