"""Sentence and clause boundaries over immutable raw text.

A sentence is often not the unit a claim lives in. The field trace behind this
contract version contained one sentence carrying two unrelated instructions:

    الان فقط خیلی کوتاه بهم توضیح بده و یادت باشه بعدا درباره اش صحبت کنیم.

Processing it as a single unit meant the deferred-topic request and the
"answer briefly" request could not be told apart, so downstream code kept one
and lost the other. Clauses give a caller somewhere to attach a claim, a
polarity, and a referential marker.

This is deliberately not a parser. It applies a small set of high-precision
deterministic boundary rules and emits a warning wherever it can see a boundary
it is not confident enough to take, so an ambiguous split is visible in the
output instead of being silently invented.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from enum import StrEnum

from memcore.textsemantics.blocks import BlockKind, scan_blocks
from memcore.textsemantics.contract import (
    DEFAULT_NORMALIZATION_CONTRACT,
    NormalizationContract,
)
from memcore.textsemantics.normalize import NormalizedText, normalize_with_mapping
from memcore.textsemantics.tokens import Token, find_any_phrase, tokenize

SEGMENTATION_CONTRACT_VERSION = "memorist.text.segmentation.v1"

# Terminators that end a sentence when followed by whitespace or end of text.
SENTENCE_TERMINATORS = frozenset(".!?؟۔‼⁇⁈⁉。！？")

# Terminators that end a clause but not necessarily a sentence.
CLAUSE_TERMINATORS = frozenset(";؛")

CLOSING_QUOTES = frozenset("\"'”»’)]}")

# Trailing tokens that keep a period from ending a sentence.
ABBREVIATIONS = frozenset(
    {"e.g", "i.e", "etc", "vs", "mr", "mrs", "ms", "dr", "prof", "fig", "no", "al", "inc", "ltd"}
)

# Conjunctions that *may* join two clauses and equally may join two noun
# phrases. Splitting on every one of them would shred
# "سرعت و عملکرد و تناسب با برنامه نویسی" into fragments, so a split needs a
# guard (see ``_conjunction_splits``).
COORDINATING_CONJUNCTIONS = frozenset({"و", "and"})

# Connectives that reliably open a new clause in both languages. Unlike the
# coordinating conjunctions above, these do not join noun phrases, so no guard
# is needed.
CONTRASTIVE_CONNECTIVES = frozenset({"اما", "ولی", "بنابراین", "however", "therefore"})

# Closed lexicon of clause-final verb forms. This is a word list in the same
# spirit as the negation lexicon -- not a stemmer, not a morphological
# analyser. It is used only as evidence that a coordinating conjunction sits
# between two clauses rather than between two nouns, and it is deliberately
# under-inclusive: a miss produces an unsplit clause plus a warning, never a
# wrong split.
CLAUSE_FINAL_VERBS = frozenset(
    {
        # Persian
        "بده",
        "بدهید",
        "باشه",
        "باشد",
        "بمونه",
        "بماند",
        "کن",
        "کنم",
        "کنی",
        "کنیم",
        "کنید",
        "کنند",
        "کرد",
        "کردم",
        "کردیم",
        "کردند",
        "کرده",
        "میکنم",
        "میکنیم",
        "میکنی",
        "میکنید",
        "دارد",
        "دارم",
        "داریم",
        "دارید",
        "دارند",
        "داشت",
        "داشتیم",
        "است",
        "هست",
        "هستم",
        "هستیم",
        "هستند",
        "نیست",
        "نیستند",
        "شد",
        "شده",
        "شود",
        "میشود",
        "بود",
        "بودم",
        "بودیم",
        "بودند",
        "گرفتیم",
        "گرفت",
        "بدونم",
        "بدانم",
        "بگو",
        "بگویید",
        "رفت",
        "رفتیم",
        "آمد",
        "گفت",
        "گفتم",
        # English
        "is",
        "are",
        "was",
        "were",
        "do",
        "does",
        "did",
        "have",
        "has",
        "had",
        "will",
        "can",
        "could",
        "should",
        "must",
        "works",
        "worked",
    }
)

# Imperative and request markers. Presence makes a clause an instruction rather
# than a statement about the world, which is what lets a caller keep the two
# apart instead of storing "explain briefly" as a fact.
IMPERATIVE_MARKERS: tuple[str, ...] = (
    # Persian
    "توضیح بده",
    "توضیح بدهید",
    "یادت باشه",
    "یادت بمونه",
    "یادت باشد",
    "به یاد داشته باش",
    "فراموش نکن",
    "فراموش نکنید",
    "خلاصه کن",
    "بنویس",
    "بگو",
    "بهم بگو",
    "نشون بده",
    "نشان بده",
    "بفرست",
    "لطفا",
    # English
    "explain",
    "tell me",
    "remember",
    "please",
    "show me",
    "write",
    "summarize",
    "give me",
    "let me know",
    "remind me",
)

QUESTION_MARKS = frozenset("?؟⁇⁈⁉？")

MAX_ABBREVIATION_LOOKBACK = 16


class ClauseKind(StrEnum):
    """What a clause does, as far as deterministic rules can tell."""

    STATEMENT = "statement"
    INSTRUCTION = "instruction"
    QUESTION = "question"
    EXPLANATION = "explanation"
    CODE = "code"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class SentenceSpan:
    """One sentence, addressable in raw and normalized coordinates."""

    index: int
    raw_start: int
    raw_end: int
    text: str
    normalized_start: int | None
    normalized_end: int | None
    block_index: int
    is_code: bool
    language_hints: tuple[str, ...]

    @property
    def sentence_id(self) -> str:
        return f"s{self.index}"


@dataclass(frozen=True)
class ClauseSpan:
    """One clause inside a sentence.

    ``clause_id`` is stable for a given raw text and contract version, which is
    all a single-message consumer needs. Binding a clause to a message or
    text-unit identity is the integrating caller's job -- this module never sees
    an identity and must not invent one.
    """

    index: int
    sentence_index: int
    block_index: int
    kind: ClauseKind
    raw_start: int
    raw_end: int
    text: str
    normalized_start: int | None
    normalized_end: int | None
    boundary_reason: str
    is_code: bool

    @property
    def clause_id(self) -> str:
        return f"c{self.index}"


def segment_sentences(
    raw: str,
    normalized: NormalizedText | None = None,
    contract: NormalizationContract = DEFAULT_NORMALIZATION_CONTRACT,
) -> tuple[tuple[SentenceSpan, ...], tuple[str, ...]]:
    """Split ``raw`` into sentences, returning the spans and any warnings."""

    resolved = normalized if normalized is not None else normalize_with_mapping(raw, contract)
    warnings: list[str] = []
    sentences: list[SentenceSpan] = []
    for block_index, block in enumerate(scan_blocks(raw)):
        if block.kind is BlockKind.CODE:
            span = _trim(raw, block.start, block.end)
            if span is None:
                continue
            sentences.append(
                _sentence(raw, resolved, len(sentences), span, block_index, is_code=True)
            )
            continue
        for span in _prose_sentence_ranges(raw, block.start, block.end, warnings):
            sentences.append(
                _sentence(raw, resolved, len(sentences), span, block_index, is_code=False)
            )
    return tuple(sentences), tuple(warnings)


def segment_clauses(
    raw: str,
    sentences: tuple[SentenceSpan, ...],
    normalized: NormalizedText | None = None,
    contract: NormalizationContract = DEFAULT_NORMALIZATION_CONTRACT,
) -> tuple[tuple[ClauseSpan, ...], tuple[str, ...]]:
    """Split each sentence into clauses, returning the spans and any warnings."""

    resolved = normalized if normalized is not None else normalize_with_mapping(raw, contract)
    warnings: list[str] = []
    clauses: list[ClauseSpan] = []
    # Tokenized once for the whole text. Tokenizing per sentence would re-scan
    # the entire string for every sentence, which is quadratic and shows up as
    # seconds on a long document.
    index = TokenIndex.build(tokenize(resolved))
    for sentence in sentences:
        if sentence.is_code:
            clauses.append(
                _clause(
                    raw,
                    resolved,
                    len(clauses),
                    sentence,
                    (sentence.raw_start, sentence.raw_end),
                    ClauseKind.CODE,
                    "code_block",
                )
            )
            continue
        for span, reason in _clause_ranges(raw, resolved, sentence, index, warnings):
            kind = _classify(raw, resolved, span, reason)
            clauses.append(_clause(raw, resolved, len(clauses), sentence, span, kind, reason))
    return tuple(clauses), tuple(warnings)


@dataclass(frozen=True)
class TokenIndex:
    """Tokens plus a start-offset index, so a range lookup is a bisection.

    Built once per text. Filtering the full stream on every clause instead is
    what turns whole-text analysis quadratic, which is measurable in seconds on
    a long document rather than being merely untidy.
    """

    tokens: tuple[Token, ...]
    starts: tuple[int, ...]

    @classmethod
    def build(cls, tokens: tuple[Token, ...]) -> TokenIndex:
        return cls(tokens=tokens, starts=tuple(token.raw_start for token in tokens))

    def in_raw_range(self, raw_start: int, raw_end: int) -> list[Token]:
        """Tokens lying entirely within ``[raw_start, raw_end)``."""

        first = bisect_left(self.starts, raw_start)
        last = bisect_right(self.starts, raw_end)
        return [token for token in self.tokens[first:last] if token.raw_end <= raw_end]


def language_hints(text: str) -> tuple[str, ...]:
    """Script hints present in ``text``, as stable sorted codes.

    Script detection, not language identification: Persian and Arabic share a
    script and this function does not pretend to tell them apart. ``und`` means
    no letters from either family were found.
    """

    hints: list[str] = []
    if any("؀" <= char <= "ۿ" or "ݐ" <= char <= "ݿ" for char in text):
        hints.append("fa")
    if any(char.isascii() and char.isalpha() for char in text):
        hints.append("en")
    return tuple(hints) if hints else ("und",)


def _prose_sentence_ranges(
    raw: str,
    start: int,
    end: int,
    warnings: list[str],
) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    cursor = start
    index = start
    saw_terminator = False
    while index < end:
        char = raw[index]
        if char in SENTENCE_TERMINATORS and _terminates(raw, index, end):
            stop = _consume_closing(raw, index + 1, end)
            span = _trim(raw, cursor, stop)
            if span is not None:
                ranges.append(span)
            saw_terminator = True
            cursor = stop
            index = stop
            continue
        blank = _blank_line_end(raw, index, end)
        if blank is not None:
            span = _trim(raw, cursor, index)
            if span is not None:
                ranges.append(span)
            cursor = blank
            index = blank
            continue
        index += 1
    tail = _trim(raw, cursor, end)
    if tail is not None:
        ranges.append(tail)
        if not saw_terminator and not ranges[:-1]:
            warnings.append("sentence_without_terminator")
    return ranges


def _terminates(raw: str, index: int, end: int) -> bool:
    """Whether a terminator character actually ends a sentence here."""

    char = raw[index]
    if char == ".":
        before = raw[index - 1] if index > 0 else ""
        after = raw[index + 1] if index + 1 < end else ""
        # "5.4", "v1.2", "example.com": a period glued to content on both sides
        # is part of a written identifier, not a sentence end.
        if before.isalnum() and after.isalnum():
            return False
        if _preceding_abbreviation(raw, index):
            return False
    stop = _consume_closing(raw, index + 1, end)
    if stop >= end:
        return True
    return raw[stop].isspace()


def _preceding_abbreviation(raw: str, index: int) -> bool:
    window = raw[max(0, index - MAX_ABBREVIATION_LOOKBACK) : index]
    parts = window.split()
    if not parts:
        return False
    return parts[-1].lower().strip(".") in ABBREVIATIONS


def _consume_closing(raw: str, index: int, end: int) -> int:
    while index < end and raw[index] in CLOSING_QUOTES:
        index += 1
    return index


def _blank_line_end(raw: str, index: int, end: int) -> int | None:
    """End offset of a whitespace run holding two or more newlines."""

    if not raw[index].isspace():
        return None
    stop = index
    newlines = 0
    while stop < end and raw[stop].isspace():
        if raw[stop] == "\n":
            newlines += 1
        stop += 1
    return stop if newlines >= 2 else None


def _clause_ranges(
    raw: str,
    resolved: NormalizedText,
    sentence: SentenceSpan,
    index: TokenIndex,
    warnings: list[str],
) -> list[tuple[tuple[int, int], str]]:
    """Clause ranges inside one sentence, each with the reason it started."""

    cuts = sorted(
        [
            *_punctuation_cuts(raw, sentence, index, warnings),
            *_conjunction_cuts(raw, sentence, index, warnings),
        ],
        key=lambda cut: cut[0],
    )
    ranges: list[tuple[tuple[int, int], str]] = []
    cursor = sentence.raw_start
    reason = "sentence_start"
    for offset, next_reason in cuts:
        if offset <= cursor or offset >= sentence.raw_end:
            continue
        span = _trim(raw, cursor, offset)
        if span is not None:
            ranges.append((span, reason))
            reason = next_reason
        cursor = offset
    tail = _trim(raw, cursor, sentence.raw_end)
    if tail is not None:
        ranges.append((tail, reason))
    if not ranges:
        return [((sentence.raw_start, sentence.raw_end), "sentence_start")]
    return ranges


def _punctuation_cuts(
    raw: str,
    sentence: SentenceSpan,
    index_: TokenIndex,
    warnings: list[str],
) -> list[tuple[int, str]]:
    cuts: list[tuple[int, str]] = []
    for index in range(sentence.raw_start, sentence.raw_end):
        char = raw[index]
        if char == ":" and _colon_explains(raw, index, sentence.raw_end):
            cuts.append((index + 1, "colon_explanation"))
        elif char in CLAUSE_TERMINATORS:
            cuts.append((index + 1, "clause_terminator"))
        elif char == "\n":
            cuts.append(_line_break_cut(sentence, index, index_, warnings))
    return cuts


def _line_break_cut(
    sentence: SentenceSpan,
    index: int,
    tokens: TokenIndex,
    warnings: list[str],
) -> tuple[int, str]:
    """Classify a newline that falls *inside* a sentence.

    A newline between two sentences is already a sentence boundary and never
    reaches here. What reaches here is a soft wrap, and a soft wrap is not
    evidence of a clause boundary -- "the pipeline runs on\\nFriday afternoons"
    is one proposition split by a text editor, not two.

    Splitting anyway is still useful, because a genuine list or a hard-wrapped
    clause does break here. So the split is taken either way, and the same guard
    the coordinating conjunctions use decides whether it is *verified*: a
    clause-final verb before the break means the first fragment really did end.
    Otherwise the boundary is reported as unverified and the fragment is left
    ``UNKNOWN``, which keeps a half-sentence out of the antecedent candidates
    where it would read as a proposition the writer never made.
    """

    preceding = tokens.in_raw_range(sentence.raw_start, index)
    if preceding and preceding[-1].key in CLAUSE_FINAL_VERBS:
        return index + 1, "line_break_after_verb"
    warnings.append("line_break_split_unverified")
    return index + 1, "line_break_unverified"


def _colon_explains(raw: str, index: int, end: int) -> bool:
    """A colon introduces an explanation only when prose follows it.

    ``https://`` and ``10:30`` glue the colon to content, so requiring
    whitespace after it is enough to leave both alone.
    """

    if index + 1 >= end:
        return False
    if not raw[index + 1].isspace():
        return False
    return bool(raw[index + 1 : end].strip())


def _conjunction_cuts(
    raw: str,
    sentence: SentenceSpan,
    index: TokenIndex,
    warnings: list[str],
) -> list[tuple[int, str]]:
    """Guarded clause boundaries at conjunctions and contrastive connectives.

    A coordinating conjunction is taken as a clause boundary only when the
    preceding token is a known clause-final verb form, or when a comma sits in
    front of it. Everything else is left joined and reported, because "سرعت و
    عملکرد" is one noun phrase and splitting it would fabricate a proposition
    the writer never made.
    """

    tokens = index.in_raw_range(sentence.raw_start, sentence.raw_end)
    cuts: list[tuple[int, str]] = []
    for position, token in enumerate(tokens):
        if token.key in CONTRASTIVE_CONNECTIVES:
            if position > 0:
                cuts.append((token.raw_start, "contrastive_connective"))
            continue
        if token.key not in COORDINATING_CONJUNCTIONS:
            continue
        if position == 0 or position + 1 >= len(tokens):
            continue
        previous = tokens[position - 1]
        gap = raw[previous.raw_end : token.raw_start]
        if previous.key in CLAUSE_FINAL_VERBS:
            cuts.append((token.raw_start, "coordinating_conjunction_after_verb"))
        elif "،" in gap or "," in gap:
            cuts.append((token.raw_start, "coordinating_conjunction_after_comma"))
        else:
            warnings.append("coordinating_conjunction_not_split")
    return cuts


def _classify(
    raw: str,
    resolved: NormalizedText,
    span: tuple[int, int],
    reason: str,
) -> ClauseKind:
    start, end = span
    text = raw[start:end]
    if text.rstrip().endswith(tuple(QUESTION_MARKS)):
        return ClauseKind.QUESTION
    normalized_span = resolved.normalized_span(start, end)
    if normalized_span is None:
        return ClauseKind.UNKNOWN
    slice_text = resolved.text[normalized_span[0] : normalized_span[1]]
    if find_any_phrase(slice_text, IMPERATIVE_MARKERS) is not None:
        return ClauseKind.INSTRUCTION
    if reason == "colon_explanation":
        return ClauseKind.EXPLANATION
    if not slice_text.strip():
        return ClauseKind.UNKNOWN
    if reason == "line_break_unverified":
        # A fragment carved out of a soft wrap is not a proposition until
        # something confirms the boundary. Leaving it UNKNOWN keeps it out of
        # SUBJECT_MATTER_KINDS, so it is never offered as an antecedent.
        return ClauseKind.UNKNOWN
    return ClauseKind.STATEMENT


def _sentence(
    raw: str,
    resolved: NormalizedText,
    index: int,
    span: tuple[int, int],
    block_index: int,
    *,
    is_code: bool,
) -> SentenceSpan:
    start, end = span
    normalized = resolved.normalized_span(start, end)
    text = raw[start:end]
    return SentenceSpan(
        index=index,
        raw_start=start,
        raw_end=end,
        text=text,
        normalized_start=normalized[0] if normalized else None,
        normalized_end=normalized[1] if normalized else None,
        block_index=block_index,
        is_code=is_code,
        language_hints=language_hints(text),
    )


def _clause(
    raw: str,
    resolved: NormalizedText,
    index: int,
    sentence: SentenceSpan,
    span: tuple[int, int],
    kind: ClauseKind,
    reason: str,
) -> ClauseSpan:
    start, end = span
    normalized = resolved.normalized_span(start, end)
    return ClauseSpan(
        index=index,
        sentence_index=sentence.index,
        block_index=sentence.block_index,
        kind=kind,
        raw_start=start,
        raw_end=end,
        text=raw[start:end],
        normalized_start=normalized[0] if normalized else None,
        normalized_end=normalized[1] if normalized else None,
        boundary_reason=reason,
        is_code=sentence.is_code,
    )


def _trim(raw: str, start: int, end: int) -> tuple[int, int] | None:
    """Tighten a range onto non-whitespace, or ``None`` if nothing is left."""

    while start < end and raw[start].isspace():
        start += 1
    while end > start and raw[end - 1].isspace():
        end -= 1
    return (start, end) if start < end else None
