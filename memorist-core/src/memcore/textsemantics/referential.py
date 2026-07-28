"""A non-authoritative routing hint for bounded conversation context.

Nothing here resolves reference. It reports closed-class deictics and provides a
history-window policy whose baseline is never zero. Hints may expand context;
they may never be the sole reason any context is sent.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from memcore.textsemantics.contract import (
    DEFAULT_NORMALIZATION_CONTRACT,
    NormalizationContract,
)
from memcore.textsemantics.normalize import NormalizedText, normalize_with_mapping
from memcore.textsemantics.tokens import tokenize

REFERENTIAL_CONTRACT_VERSION = "memorist.text.referential.v2"
NON_AUTHORITATIVE: Final = "non_authoritative"
BASELINE_SEMANTIC_HISTORY_UNITS: Final = 2
EXPANDED_SEMANTIC_HISTORY_UNITS: Final = 6

DEICTIC_TOKENS: Final[frozenset[str]] = frozenset(
    {
        "این",
        "آن",
        "همان",
        "همین",
        "چنین",
        "اینها",
        "آنها",
        "ایشان",
        "اش",
        "شان",
        "شون",
        "this",
        "that",
        "these",
        "those",
        "it",
        "them",
        "they",
        "such",
    }
)


@dataclass(frozen=True)
class ContextDependencyHint:
    """A closed-class deictic occurs at ``[raw_start, raw_end)``."""

    raw_start: int
    raw_end: int
    evidence: str
    normalized_text: str
    authority: str = NON_AUTHORITATIVE

    @property
    def is_authoritative(self) -> bool:
        return False


def detect_context_dependency(
    raw: str,
    normalized: NormalizedText | None = None,
    contract: NormalizationContract = DEFAULT_NORMALIZATION_CONTRACT,
) -> tuple[ContextDependencyHint, ...]:
    """Report closed-class deictics, with exact raw evidence.

    Fenced code is skipped. The result says only that wider context may help; it
    does not identify a referent, candidate, or semantic unit.
    """

    resolved = normalized if normalized is not None else normalize_with_mapping(raw, contract)
    return tuple(
        ContextDependencyHint(
            raw_start=token.raw_start,
            raw_end=token.raw_end,
            evidence=resolved.raw[token.raw_start : token.raw_end],
            normalized_text=token.text,
        )
        for token in tokenize(resolved)
        if token.key in DEICTIC_TOKENS
    )


def requires_conversation_context(hints: tuple[ContextDependencyHint, ...]) -> bool:
    """Whether hints justify expanding the non-zero baseline history window."""

    return bool(hints)


def semantic_history_window_size(
    hints: tuple[ContextDependencyHint, ...],
    *,
    baseline_units: int = BASELINE_SEMANTIC_HISTORY_UNITS,
    expanded_units: int = EXPANDED_SEMANTIC_HISTORY_UNITS,
) -> int:
    """Return a bounded history size for the model semantic node.

    The baseline must be at least one unit. Deictic hints can expand the window,
    never reduce it to zero. This avoids making a closed-class detector a
    completeness authority: ellipsis and implicit continuation can require
    context even when no pronoun is present.
    """

    if baseline_units < 1:
        raise ValueError("baseline_units must be at least 1")
    if expanded_units < baseline_units:
        raise ValueError("expanded_units must be greater than or equal to baseline_units")
    return expanded_units if hints else baseline_units
