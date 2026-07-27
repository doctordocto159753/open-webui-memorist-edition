"""A routing hint: this message probably needs conversation context.

Nothing here analyses reference. It reports that a closed-class deictic word --
a pronoun or a demonstrative -- occurs at a given offset, and stops.

That is a deliberate retreat. An earlier version of this module tried to decide
which spans a marker could point at, ranked them, filtered out "instruction"
clauses, and labelled its own confidence. All of that is reference resolution,
and reference resolution needs to weigh what the conversation has been about --
which is what the model-equipped semantic node is for. Hand-written rules
reached plausible answers on the examples they were written against and
confidently wrong ones everywhere else.

What survives is useful precisely because it claims so little:

* pronouns and demonstratives are a **closed class** in both target languages,
  so unlike verb forms the list does not grow with every new example;
* "a deictic occurs here" is an observation about characters, not meaning;
* the only intended consumer is routing -- a message with deictics should be
  sent to the model with a wider context window than one without.

Every record is stamped ``non_authoritative``. No memory may be created from it,
and a test enforces that no production path consumes it for that purpose.
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

#: Stamped on every record this module produces. Downstream code checks it.
NON_AUTHORITATIVE: Final = "non_authoritative"

# Closed-class deictics. A closed class does not grow: Persian and English are
# not going to acquire a new pronoun, which is exactly why this list is safe to
# hard-code where a list of verb forms was not.
DEICTIC_TOKENS: Final[frozenset[str]] = frozenset(
    {
        # Persian demonstratives and bound pronouns
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
        # English
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
    """A closed-class deictic occurs at ``[raw_start, raw_end)``.

    Not a marker type, not a confidence, not a candidate antecedent. Those
    fields existed here once and every one of them was a decision this layer had
    no basis to make.
    """

    raw_start: int
    raw_end: int
    evidence: str
    normalized_text: str
    authority: str = NON_AUTHORITATIVE

    @property
    def is_authoritative(self) -> bool:
        """Always ``False``. Present so a caller cannot forget to ask."""

        return False


def detect_context_dependency(
    raw: str,
    normalized: NormalizedText | None = None,
    contract: NormalizationContract = DEFAULT_NORMALIZATION_CONTRACT,
) -> tuple[ContextDependencyHint, ...]:
    """Report closed-class deictics, with exact raw evidence.

    Fenced code is skipped: ``this`` in a snippet is a variable, not a pronoun.
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
    """Whether the model should be given a wider context window.

    This is the whole intended use. It informs how much history to send, and
    nothing about what the text means.
    """

    return bool(hints)
