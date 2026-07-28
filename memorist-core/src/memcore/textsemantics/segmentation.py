"""Sentence spans over immutable raw text -- typography, not syntax.

This module answers one narrow question: **where did the writer put explicit
boundary marks?** A full stop, a question mark, a blank line, a fenced block.
Those are characters a person typed on purpose, so reading them is transcription
rather than interpretation.

It deliberately does **not** answer:

* where one proposition ends and the next begins;
* whether a clause is a statement, an instruction, or an explanation;
* whether ``و`` joins two clauses or two nouns;
* whether ``است`` or ``is`` closes a clause.

Those are questions about meaning, and meaning is the model-equipped semantic
node's job. An earlier version of this module tried to answer them with
hand-written lexicons of verb forms, conjunctions, and imperatives. Every new
example demanded another lexicon entry, and every entry created a new way to be
confidently wrong -- which is the signature of responsibility sitting in the
wrong layer. Deterministic code should enforce truth boundaries; the model
should analyse language.

What remains here is the envelope the model's answer gets validated against:
exact offsets into text nobody rewrote.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass

from memcore.textsemantics.blocks import BlockKind, scan_blocks
from memcore.textsemantics.contract import (
    DEFAULT_NORMALIZATION_CONTRACT,
    NormalizationContract,
)
from memcore.textsemantics.normalize import NormalizedText, normalize_with_mapping
from memcore.textsemantics.tokens import Token

SEGMENTATION_CONTRACT_VERSION = "memorist.text.segmentation.v2"

# Marks that end a sentence when the writer also left whitespace after them.
SENTENCE_TERMINATORS = frozenset(".!?؟۔‼⁇⁈⁉。！？")

CLOSING_QUOTES = frozenset("\"'”»’)]}")

# Trailing tokens that keep a period from ending a sentence. Not linguistics --
# just the short list of forms where a period is punctuation inside a word.
ABBREVIATIONS = frozenset(
    {"e.g", "i.e", "etc", "vs", "mr", "mrs", "ms", "dr", "prof", "fig", "no", "al", "inc", "ltd"}
)

# Bullets that open a hand-written list item. Explicit structure, typed on
# purpose, which is why it counts where a soft wrap does not.
LIST_BULLETS = frozenset("-*+•—–")

MAX_ABBREVIATION_LOOKBACK = 16


@dataclass(frozen=True)
class SentenceSpan:
    """One span bounded by explicit punctuation or explicit structure.

    "Sentence" here means "what the writer's own boundary marks delimit". It
    carries no claim about propositions: one span may hold several, and a
    proposition may run across two. Deciding that is the model's job.
    """

    index: int
    raw_start: int
    raw_end: int
    text: str
    normalized_start: int | None
    normalized_end: int | None
    block_index: int
    is_code: bool
    boundary_reason: str
    language_hints: tuple[str, ...]

    @property
    def sentence_id(self) -> str:
        return f"s{self.index}"


@dataclass(frozen=True)
class TokenIndex:
    """Tokens plus a start-offset index, so a range lookup is a bisection.

    Built once per text. Filtering the full stream on every lookup instead is
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


def segment_sentences(
    raw: str,
    normalized: NormalizedText | None = None,
    contract: NormalizationContract = DEFAULT_NORMALIZATION_CONTRACT,
) -> tuple[tuple[SentenceSpan, ...], tuple[str, ...]]:
    """Split ``raw`` on explicit boundary marks, returning spans and warnings."""

    resolved = normalized if normalized is not None else normalize_with_mapping(raw, contract)
    warnings: list[str] = []
    sentences: list[SentenceSpan] = []
    for block_index, block in enumerate(scan_blocks(raw)):
        if block.kind is BlockKind.CODE:
            span = _trim(raw, block.start, block.end)
            if span is None:
                continue
            sentences.append(
                _sentence(
                    raw, resolved, len(sentences), span, block_index, "code_block", is_code=True
                )
            )
            continue
        for span, reason in _prose_ranges(raw, block.start, block.end, warnings):
            sentences.append(
                _sentence(raw, resolved, len(sentences), span, block_index, reason, is_code=False)
            )
    return tuple(sentences), tuple(warnings)


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


def _prose_ranges(
    raw: str,
    start: int,
    end: int,
    warnings: list[str],
) -> list[tuple[tuple[int, int], str]]:
    """Ranges delimited by terminators, blank lines, and list markers.

    A bare newline is **not** a boundary. It is usually a text editor wrapping a
    line, and treating it as structure splits one proposition into two halves
    that then look like two propositions. When a wrap is seen and declined, that
    is recorded rather than guessed at.
    """

    ranges: list[tuple[tuple[int, int], str]] = []
    cursor = start
    index = start
    reason = "block_start"
    saw_boundary = False
    while index < end:
        char = raw[index]
        if char in SENTENCE_TERMINATORS and _terminates(raw, index, end):
            stop = _consume_closing(raw, index + 1, end)
            _append(ranges, raw, cursor, stop, reason)
            saw_boundary = True
            cursor, index, reason = stop, stop, "sentence_terminator"
            continue
        if char == "\n":
            blank = _blank_run_end(raw, index, end)
            if blank is not None:
                _append(ranges, raw, cursor, index, reason)
                saw_boundary = True
                cursor, index, reason = blank, blank, "blank_line"
                continue
            if _opens_list_item(raw, index + 1, end):
                _append(ranges, raw, cursor, index, reason)
                saw_boundary = True
                cursor, index, reason = index + 1, index + 1, "list_item"
                continue
            warnings.append("line_break_not_a_boundary")
        index += 1
    if _append(ranges, raw, cursor, end, reason) and not saw_boundary:
        warnings.append("no_explicit_boundary_mark")
    return ranges


def _append(
    ranges: list[tuple[tuple[int, int], str]],
    raw: str,
    start: int,
    end: int,
    reason: str,
) -> bool:
    span = _trim(raw, start, end)
    if span is None:
        return False
    ranges.append((span, reason))
    return True


def _terminates(raw: str, index: int, end: int) -> bool:
    """Whether a terminator character actually ends a span here."""

    char = raw[index]
    if char == ".":
        before = raw[index - 1] if index > 0 else ""
        after = raw[index + 1] if index + 1 < end else ""
        # "5.4", "v1.2", "example.com": a period glued to content on both sides
        # is part of a written identifier, not a boundary mark.
        if before.isalnum() and after.isalnum():
            return False
        if _preceding_abbreviation(raw, index):
            return False
        if _is_enumerator(raw, index):
            return False
    stop = _consume_closing(raw, index + 1, end)
    if stop >= end:
        return True
    return raw[stop].isspace()


def _is_enumerator(raw: str, index: int) -> bool:
    """Whether this period belongs to a line-leading list number.

    ``2.`` opening a line is an enumerator, not the end of the span before it.
    """

    prefix = raw[raw.rfind("\n", 0, index) + 1 : index].strip()
    return prefix.isdigit() and prefix != ""


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


def _blank_run_end(raw: str, index: int, end: int) -> int | None:
    """End offset of a whitespace run holding two or more newlines."""

    stop = index
    newlines = 0
    while stop < end and raw[stop].isspace():
        if raw[stop] == "\n":
            newlines += 1
        stop += 1
    return stop if newlines >= 2 else None


def _opens_list_item(raw: str, start: int, end: int) -> bool:
    """Whether the line beginning at ``start`` is an explicit list item."""

    cursor = start
    while cursor < end and raw[cursor] in " \t":
        cursor += 1
    if cursor >= end:
        return False
    if raw[cursor] in LIST_BULLETS:
        return cursor + 1 < end and raw[cursor + 1] in " \t"
    digits = cursor
    while digits < end and raw[digits].isdigit():
        digits += 1
    if digits == cursor:
        return False
    return digits + 1 < end and raw[digits] in ".)" and raw[digits + 1] in " \t"


def _sentence(
    raw: str,
    resolved: NormalizedText,
    index: int,
    span: tuple[int, int],
    block_index: int,
    boundary_reason: str,
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
        boundary_reason=boundary_reason,
        language_hints=language_hints(text),
    )


def _trim(raw: str, start: int, end: int) -> tuple[int, int] | None:
    """Tighten a range onto non-whitespace, or ``None`` if nothing is left."""

    while start < end and raw[start].isspace():
        start += 1
    while end > start and raw[end - 1].isspace():
        end -= 1
    return (start, end) if start < end else None
