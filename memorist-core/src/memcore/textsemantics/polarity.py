"""Claim polarity, extracted independently of extraction confidence.

Polarity answers "does this claim assert or deny?". Confidence answers "how
sure are we that we extracted the claim correctly?". A user who says
"we never deploy on Friday" states something as certainly as one who says
"we deploy on Friday", so the two must not share a score.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from memcore.textsemantics.contract import NORMALIZATION_CONTRACT_VERSION
from memcore.textsemantics.normalize import NormalizedText, normalize_with_mapping
from memcore.textsemantics.tokens import find_any_phrase, tokenize

POLARITY_CONTRACT_VERSION = "memorist.text.polarity.v1"


class Polarity(StrEnum):
    """Whether a claim is asserted, denied, or undetermined."""

    AFFIRMED = "affirmed"
    NEGATED = "negated"
    UNKNOWN = "unknown"


NEGATION_PHRASES: tuple[str, ...] = (
    # English
    "not",
    "no",
    "never",
    "none",
    "nothing",
    "nobody",
    "neither",
    "nor",
    "without",
    "cannot",
    "do not",
    "does not",
    "did not",
    "will not",
    "would not",
    "should not",
    "must not",
    "is not",
    "are not",
    "was not",
    "were not",
    "has not",
    "have not",
    "had not",
    "dont",
    "doesnt",
    "didnt",
    "wont",
    "cant",
    "isnt",
    "arent",
    "wasnt",
    "werent",
    "hasnt",
    "havent",
    "hadnt",
    "shouldnt",
    "wouldnt",
    "mustnt",
    "couldnt",
    # Persian
    "نباید",
    "هرگز",
    "نکن",
    "نکنید",
    "نکنیم",
    "نیست",
    "نیستند",
    "ندارد",
    "ندارم",
    "نداریم",
    "ندارید",
    "بدون",
    "خیر",
    "نخواهم",
    "نخواهد",
    "نکرده",
    "نشده",
    # Persian negates a continuous verb with a bound "نمی" prefix. Written with
    # the usual ZWNJ ("نمی‌کنیم") it tokenizes to a standalone "نمی", which is
    # never a word on its own, so the token itself is the negation marker.
    "نمی",
)

# English contractions tokenize to two tokens because the apostrophe is a
# boundary: "don't" -> ("don", "t"). Listing them as phrases keeps the
# apostrophe form matching without reintroducing substring search.
NEGATION_CONTRACTIONS: tuple[str, ...] = (
    "don t",
    "doesn t",
    "didn t",
    "won t",
    "can t",
    "isn t",
    "aren t",
    "wasn t",
    "weren t",
    "hasn t",
    "haven t",
    "hadn t",
    "shouldn t",
    "wouldn t",
    "mustn t",
    "couldn t",
)

# The ZWNJ spelling is handled as a plain token above. This rule catches the
# joined spelling ("نمیخواهم"), and is anchored at the start of a whole token
# so it can never match mid-token the way a substring search would.
NEGATION_TOKEN_PREFIXES: tuple[str, ...] = ("نمی",)

HYPOTHETICAL_PHRASES: tuple[str, ...] = (
    "if",
    "suppose",
    "hypothetically",
    "assuming",
    "اگر",
    "فرض کنیم",
    "فرض کنید",
    "احتمالا",
)


@dataclass(frozen=True)
class PolarityResult:
    """Polarity plus the exact raw evidence that decided it."""

    polarity: Polarity
    marker: str | None = None
    evidence: str | None = None
    raw_start: int | None = None
    raw_end: int | None = None
    contract_version: str = POLARITY_CONTRACT_VERSION
    normalization_contract_version: str = NORMALIZATION_CONTRACT_VERSION

    @property
    def negated(self) -> bool:
        """Compatibility view for call sites that still speak in booleans."""

        return self.polarity is Polarity.NEGATED


def extract_polarity(text: NormalizedText | str) -> PolarityResult:
    """Classify a claim as affirmed, negated, or unknown.

    Text with no tokens at all (empty, whitespace, or code-only) is ``unknown``
    rather than ``affirmed``: there is no claim to assert. Text with tokens and
    no negation marker is ``affirmed``, which is a decision the detector is
    entitled to make and record.
    """

    resolved = text if isinstance(text, NormalizedText) else normalize_with_mapping(text)
    tokens = tokenize(resolved)
    if not tokens:
        return PolarityResult(polarity=Polarity.UNKNOWN)

    match = find_any_phrase(resolved, NEGATION_PHRASES + NEGATION_CONTRACTIONS)
    if match is not None:
        return PolarityResult(
            polarity=Polarity.NEGATED,
            marker=match.text,
            evidence=match.evidence,
            raw_start=match.raw_start,
            raw_end=match.raw_end,
        )

    for token in tokens:
        prefix = next(
            (item for item in NEGATION_TOKEN_PREFIXES if token.text.startswith(item)),
            None,
        )
        if prefix is None or token.text == prefix:
            continue
        return PolarityResult(
            polarity=Polarity.NEGATED,
            marker=prefix,
            evidence=resolved.raw[token.raw_start : token.raw_end],
            raw_start=token.raw_start,
            raw_end=token.raw_end,
        )

    return PolarityResult(polarity=Polarity.AFFIRMED)


def is_hypothetical(text: NormalizedText | str) -> bool:
    """Whether the text is framed as a supposition rather than a claim."""

    return find_any_phrase(text, HYPOTHETICAL_PHRASES) is not None


def polarity_from_flag(negated: bool | None) -> Polarity:
    """Read a historical ``modality.negated`` boolean as a polarity.

    ``None`` means the analyzer never recorded modality, which stays
    ``unknown``. A recorded ``False`` is an analyzer decision that no negation
    was present, so it reads as ``affirmed`` rather than being discarded.
    """

    if negated is None:
        return Polarity.UNKNOWN
    return Polarity.NEGATED if negated else Polarity.AFFIRMED


def coerce_polarity(value: object) -> Polarity:
    """Read a stored polarity value, defaulting unknown/invalid to ``unknown``."""

    if isinstance(value, Polarity):
        return value
    if isinstance(value, str):
        try:
            return Polarity(value)
        except ValueError:
            return Polarity.UNKNOWN
    return Polarity.UNKNOWN
