from __future__ import annotations

from memcore.memory_worker.semantic.factors import (
    EMOTIVE_CONTEXT,
    HIGH_PRIORITY_INSTRUCTION,
    METALINGUAL_CONTEXT,
    POETIC_CONTEXT,
)
from memcore.models import JakobsonFunction
from memcore.textsemantics import NormalizedText, normalize_with_mapping, tokenize

GREETING_TOKENS = frozenset({"سلام", "درود", "hello", "hi", "hey", "thanks"})


def classify_deterministic_function(
    text: str,
) -> tuple[JakobsonFunction, tuple[JakobsonFunction, ...], str]:
    """Classify one sentence for every deterministic Jakobson runtime.

    Lite and Full must share this decision before adapting the resulting
    annotation to their persistence models. Keeping it here prevents either
    backend from silently creating a second semantic authority.
    """

    normalized = normalize_with_mapping(text)
    if _is_phatic(normalized):
        return JakobsonFunction.PHATIC, (), "The sentence opens or maintains contact."
    has_instruction = HIGH_PRIORITY_INSTRUCTION.matches(normalized)
    has_metalanguage = METALINGUAL_CONTEXT.matches(normalized)
    if has_instruction:
        secondary = (JakobsonFunction.METALINGUAL,) if has_metalanguage else ()
        return (
            JakobsonFunction.CONATIVE,
            secondary,
            "The sentence tells the receiver what to do.",
        )
    if has_metalanguage:
        return (
            JakobsonFunction.METALINGUAL,
            (),
            "The sentence defines, translates, or comments on wording.",
        )
    if EMOTIVE_CONTEXT.matches(normalized):
        return (
            JakobsonFunction.EMOTIVE,
            (),
            "The sentence expresses sender stance or desire.",
        )
    if POETIC_CONTEXT.matches(normalized):
        return (
            JakobsonFunction.POETIC,
            (),
            "The sentence foregrounds wording or style.",
        )
    return (
        JakobsonFunction.REFERENTIAL,
        (),
        "The sentence mainly describes context or facts.",
    )


def _is_phatic(normalized: NormalizedText) -> bool:
    """A greeting is a lone greeting token, whatever punctuation surrounds it."""

    tokens = tokenize(normalized)
    return len(tokens) == 1 and tokens[0].key in GREETING_TOKENS
