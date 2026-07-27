"""Referential signals: expressions that cannot be understood on their own.

The field trace behind this contract version ended with a user writing

    میخوام بیشتر درباره این مزیت بدونم بعدا.
    الان فقط خیلی کوتاه بهم توضیح بده و یادت باشه بعدا درباره اش صحبت کنیم.

and downstream processing keeping only the instruction fragment. Nothing in the
stored representation recorded that ``این مزیت`` and ``درباره اش`` point at
something said earlier, so nothing downstream could tell that the fragment was
incomplete rather than complete-and-trivial.

This module marks those expressions and lists the spans that could be what they
point at. It stops there, deliberately:

* it never asserts which candidate is the referent;
* it never reaches across messages;
* it never creates a claim because a marker exists.

Choosing a referent needs conversation state, a candidate-completeness policy,
and the authority to be wrong in a way that corrupts a stored memory. That work
belongs to the package that owns resolution. Here, an ambiguous marker stays
ambiguous and says so.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from memcore.textsemantics.contract import (
    DEFAULT_NORMALIZATION_CONTRACT,
    NormalizationContract,
)
from memcore.textsemantics.normalize import NormalizedText, normalize_with_mapping
from memcore.textsemantics.segmentation import (
    CLAUSE_FINAL_VERBS,
    ClauseKind,
    ClauseSpan,
)
from memcore.textsemantics.tokens import Token, find_all_phrases, tokenize

REFERENTIAL_CONTRACT_VERSION = "memorist.text.referential.v1"

# How many preceding subject-matter clauses are offered as candidates. Past a
# short window the list stops being evidence and starts being noise; the
# resolving package can widen it with conversation state this module lacks.
MAX_ANTECEDENT_CANDIDATES = 5

# Multi-token expressions whose whole span is the marker. Listed explicitly
# because the head alone is not the interesting unit: "درباره اش" is a request
# to return to a topic, which is a stronger signal than the bare clitic.
REFERENTIAL_PHRASES: tuple[str, ...] = (
    "درباره اش",
    "درباره آن",
    "درباره این",
    "راجع به آن",
    "راجع به این",
    "راجع بهش",
    "در این باره",
    "در آن باره",
    "در موردش",
    "در مورد آن",
    "در مورد این",
    "چنین چیزی",
    "همین موضوع",
    "همان موضوع",
    "about it",
    "about this",
    "about that",
    "such a thing",
    "the same thing",
)

# Determiners that point outward. Each may stand alone or head a noun phrase;
# ``_extend`` decides which, so ``این مزیت`` is reported as one marker rather
# than a bare ``این`` next to an unrelated noun.
DEMONSTRATIVE_TOKENS = frozenset(
    {"این", "آن", "همان", "همین", "چنین", "this", "that", "these", "those", "such"}
)

# Pronouns that stand alone. ``اش`` and ``شان`` are bound clitics; they surface
# as separate tokens because the normalization contract treats ZWNJ as a
# boundary, which is exactly what makes them findable without substring search.
PRONOUN_TOKENS = frozenset({"اش", "شان", "آنها", "اینها", "ایشان", "it", "them", "they"})

# Markers whose reading is genuinely uncertain out of context. English "that"
# is as often a complementiser as a demonstrative, and "it" is as often
# expletive as referential. They are still reported -- a missed dependency is
# worse than a flagged one, because nothing here creates a memory -- but the
# confidence label says not to trust them as strongly.
AMBIGUOUS_MARKERS = frozenset({"that", "it", "this", "such", "they", "آن", "این"})

SUBJECT_MATTER_KINDS = frozenset({ClauseKind.STATEMENT, ClauseKind.EXPLANATION})


class MarkerType(StrEnum):
    """What kind of expression carries the dependency."""

    PRONOUN = "pronoun"
    DEMONSTRATIVE = "demonstrative"
    DEMONSTRATIVE_PHRASE = "demonstrative_phrase"
    TOPIC_REFERENCE = "topic_reference"


class ResolutionScope(StrEnum):
    """Where a resolver should look. A hint, never a decision."""

    PRIOR_CLAUSE = "prior_clause"
    PRIOR_MESSAGE = "prior_message"
    UNKNOWN = "unknown"


class ReferentialConfidence(StrEnum):
    """How certain the *marker* is -- never how certain the referent is."""

    MARKER_CERTAIN = "marker_certain"
    MARKER_AMBIGUOUS = "marker_ambiguous"


@dataclass(frozen=True)
class AntecedentCandidate:
    """A span that could be what a marker points at. Not a resolution."""

    clause_id: str
    clause_index: int
    raw_start: int
    raw_end: int
    text: str
    distance_clauses: int
    reason_codes: tuple[str, ...]


@dataclass(frozen=True)
class ReferentialMarker:
    """One context-dependent expression, with its unresolved candidates."""

    marker_type: MarkerType
    text: str
    raw_start: int
    raw_end: int
    normalized_start: int | None
    normalized_end: int | None
    clause_id: str
    clause_index: int
    requires_context: bool
    resolution_scope_hint: ResolutionScope
    antecedent_candidate_spans: tuple[AntecedentCandidate, ...]
    confidence_label: ReferentialConfidence
    reason_codes: tuple[str, ...]
    contract_version: str = REFERENTIAL_CONTRACT_VERSION


def detect_referential_markers(
    raw: str,
    clauses: tuple[ClauseSpan, ...],
    normalized: NormalizedText | None = None,
    contract: NormalizationContract = DEFAULT_NORMALIZATION_CONTRACT,
) -> tuple[tuple[ReferentialMarker, ...], tuple[str, ...]]:
    """Mark context-dependent expressions and list possible antecedent spans."""

    resolved = normalized if normalized is not None else normalize_with_mapping(raw, contract)
    warnings: list[str] = []
    spans = _candidate_spans(resolved)
    markers: list[ReferentialMarker] = []
    for raw_start, raw_end, text, marker_type in spans:
        clause = _clause_for(clauses, raw_start, raw_end)
        if clause is None:
            warnings.append("referential_marker_outside_clause")
            continue
        if clause.is_code:
            # A demonstrative inside a fenced block is source code, not a
            # pronoun: "this" in a snippet refers to an object, not to
            # something the user said.
            continue
        antecedents = _antecedents(clauses, clause)
        markers.append(
            ReferentialMarker(
                marker_type=marker_type,
                text=text,
                raw_start=raw_start,
                raw_end=raw_end,
                normalized_start=_normalized(resolved, raw_start, raw_end, 0),
                normalized_end=_normalized(resolved, raw_start, raw_end, 1),
                clause_id=clause.clause_id,
                clause_index=clause.index,
                requires_context=True,
                resolution_scope_hint=(
                    ResolutionScope.PRIOR_CLAUSE if antecedents else ResolutionScope.PRIOR_MESSAGE
                ),
                antecedent_candidate_spans=antecedents,
                confidence_label=_confidence(text, marker_type),
                reason_codes=_reason_codes(marker_type, antecedents),
            )
        )
    return tuple(markers), tuple(warnings)


def _candidate_spans(
    resolved: NormalizedText,
) -> list[tuple[int, int, str, MarkerType]]:
    """Marker spans in raw coordinates, longest-first, non-overlapping."""

    heads: list[tuple[int, int, str, MarkerType]] = []
    tokens = tokenize(resolved)
    for position, token in enumerate(tokens):
        if token.key in DEMONSTRATIVE_TOKENS:
            extended = _extend(resolved, tokens, position)
            if extended is None:
                heads.append((token.raw_start, token.raw_end, token.text, MarkerType.DEMONSTRATIVE))
            else:
                heads.append((*extended, MarkerType.DEMONSTRATIVE_PHRASE))
        elif token.key in PRONOUN_TOKENS:
            heads.append((token.raw_start, token.raw_end, token.text, MarkerType.PRONOUN))

    topics = [
        (match.raw_start, match.raw_end, match.text, MarkerType.TOPIC_REFERENCE)
        for match in find_all_phrases(resolved, REFERENTIAL_PHRASES)
        if not _truncates(match.raw_start, match.raw_end, heads)
    ]
    return _drop_overlaps([*topics, *heads])


def _truncates(
    start: int,
    end: int,
    heads: list[tuple[int, int, str, MarkerType]],
) -> bool:
    """Whether a topic phrase would cut a longer referring expression short.

    ``درباره این`` is a useful marker on its own, but in ``درباره این مزیت`` the
    expression that actually carries the dependency is ``این مزیت`` -- the noun
    is what constrains which antecedent could possibly fit. Leftmost-longest
    alone would keep the topic phrase and drop the noun, so a topic phrase that
    starts a referring expression it cannot contain yields to it. In
    ``درباره اش`` nothing extends past the phrase, so the phrase survives.
    """

    return any(start <= head_start < end < head_end for head_start, head_end, _, _ in heads)


def _extend(
    resolved: NormalizedText,
    tokens: tuple[Token, ...],
    position: int,
) -> tuple[int, int, str] | None:
    """Grow a determiner onto the noun it heads, if it heads one.

    ``این مزیت`` is one referring expression; ``این هست`` is a determiner
    followed by a verb, so the determiner stands alone. Checking the next token
    against the clause-final verb lexicon separates the two without a parser.
    Enumerating nouns is impossible, so the rule is stated over what a noun is
    *not*.
    """

    if position + 1 >= len(tokens):
        return None
    head, following = tokens[position], tokens[position + 1]
    if following.index != head.index + 1 or following.in_code:
        return None
    if following.key in CLAUSE_FINAL_VERBS or following.key in DEMONSTRATIVE_TOKENS:
        return None
    if following.key in PRONOUN_TOKENS:
        return None
    gap = resolved.text[head.end : following.start]
    if gap and not gap.isspace():
        return None
    return head.raw_start, following.raw_end, resolved.text[head.start : following.end]


def _drop_overlaps(
    found: list[tuple[int, int, str, MarkerType]],
) -> list[tuple[int, int, str, MarkerType]]:
    """Leftmost-longest wins, so a phrase absorbs the head it contains."""

    ordered = sorted(found, key=lambda item: (item[0], -item[1]))
    kept: list[tuple[int, int, str, MarkerType]] = []
    consumed = -1
    for item in ordered:
        if item[0] < consumed:
            continue
        kept.append(item)
        consumed = item[1]
    return kept


def _clause_for(
    clauses: tuple[ClauseSpan, ...],
    raw_start: int,
    raw_end: int,
) -> ClauseSpan | None:
    for clause in clauses:
        if clause.raw_start <= raw_start and raw_end <= clause.raw_end:
            return clause
    return None


def _antecedents(
    clauses: tuple[ClauseSpan, ...],
    clause: ClauseSpan,
) -> tuple[AntecedentCandidate, ...]:
    """Preceding subject-matter clauses, nearest first.

    Instruction clauses are excluded: "explain briefly" is not what "this
    advantage" refers to. Every remaining clause is offered with equal standing
    -- no ranking, because a rank would be a resolution wearing a different
    name.
    """

    candidates: list[AntecedentCandidate] = []
    for previous in reversed(clauses[: clause.index]):
        if previous.kind not in SUBJECT_MATTER_KINDS or previous.is_code:
            continue
        candidates.append(
            AntecedentCandidate(
                clause_id=previous.clause_id,
                clause_index=previous.index,
                raw_start=previous.raw_start,
                raw_end=previous.raw_end,
                text=previous.text,
                distance_clauses=clause.index - previous.index,
                reason_codes=("preceding_subject_matter_clause",),
            )
        )
        if len(candidates) >= MAX_ANTECEDENT_CANDIDATES:
            break
    return tuple(candidates)


def _confidence(text: str, marker_type: MarkerType) -> ReferentialConfidence:
    if marker_type is MarkerType.DEMONSTRATIVE and text.strip() in AMBIGUOUS_MARKERS:
        return ReferentialConfidence.MARKER_AMBIGUOUS
    if marker_type is MarkerType.PRONOUN and text.strip() in AMBIGUOUS_MARKERS:
        return ReferentialConfidence.MARKER_AMBIGUOUS
    return ReferentialConfidence.MARKER_CERTAIN


def _reason_codes(
    marker_type: MarkerType,
    antecedents: tuple[AntecedentCandidate, ...],
) -> tuple[str, ...]:
    codes = [f"marker_{marker_type.value}"]
    if not antecedents:
        codes.append("no_local_antecedent")
    elif len(antecedents) > 1:
        codes.append("multiple_antecedent_candidates")
    else:
        codes.append("single_local_antecedent")
    codes.append("unresolved_by_contract")
    return tuple(codes)


def _normalized(
    resolved: NormalizedText,
    raw_start: int,
    raw_end: int,
    side: int,
) -> int | None:
    span = resolved.normalized_span(raw_start, raw_end)
    return span[side] if span is not None else None
