from __future__ import annotations

from memcore.memory_worker.semantic.factors import (
    EMOTIVE_CONTEXT,
    HIGH_PRIORITY_INSTRUCTION,
    METALINGUAL_CONTEXT,
    POETIC_CONTEXT,
)
from memcore.models import JakobsonFunction


def classify_deterministic_function(
    text: str,
) -> tuple[JakobsonFunction, tuple[JakobsonFunction, ...], str]:
    """Classify one sentence for every deterministic Jakobson runtime.

    Lite and Full must share this decision before adapting the resulting
    annotation to their persistence models. Keeping it here prevents either
    backend from silently creating a second semantic authority.
    """

    if _is_phatic(text):
        return JakobsonFunction.PHATIC, (), "The sentence opens or maintains contact."
    has_instruction = bool(HIGH_PRIORITY_INSTRUCTION.search(text))
    has_metalanguage = bool(METALINGUAL_CONTEXT.search(text))
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
    if EMOTIVE_CONTEXT.search(text):
        return (
            JakobsonFunction.EMOTIVE,
            (),
            "The sentence expresses sender stance or desire.",
        )
    if POETIC_CONTEXT.search(text):
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


def _is_phatic(text: str) -> bool:
    stripped = text.strip().strip(" .!؟?").lower()
    return stripped in {"سلام", "درود", "hello", "hi", "hey", "thanks"}
