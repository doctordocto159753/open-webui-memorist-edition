"""Normalization with an exact normalized-to-raw offset map.

Raw evidence is immutable, so normalization may never be the thing that gets
stored as evidence. Every normalized character carries the raw ``[start, end)``
range that produced it, which lets a caller match on normalized text and still
persist the exact raw span an auditor can re-read.
"""

from __future__ import annotations

import unicodedata
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from functools import cached_property

from memcore.textsemantics.blocks import BlockKind, TextBlock, scan_blocks
from memcore.textsemantics.contract import (
    DEFAULT_NORMALIZATION_CONTRACT,
    NormalizationContract,
    ZwnjPolicy,
)

ZWNJ = "‌"
ZWJ = "‍"
TATWEEL = "ـ"

# Blocks that hold Arabic-script characters. Restricting diacritic removal to
# these blocks is what keeps it safe: a blanket "drop every combining mark"
# rule would turn "résumé" into "resume" and change meaning in languages this
# service also has to handle.
_ARABIC_BLOCKS = ((0x0600, 0x06FF), (0x0750, 0x077F), (0x08A0, 0x08FF), (0xFE70, 0xFEFF))


def _arabic_marks() -> frozenset[str]:
    """Non-spacing marks inside the Arabic blocks, derived from Unicode data."""

    return frozenset(
        chr(code_point)
        for start, end in _ARABIC_BLOCKS
        for code_point in range(start, end + 1)
        if unicodedata.category(chr(code_point)) == "Mn"
    )


ARABIC_DIACRITICS = _arabic_marks()

PERSIAN_LETTER_MAP = {
    "ي": "ی",  # ARABIC YEH -> FARSI YEH
    "ى": "ی",  # ALEF MAKSURA -> FARSI YEH
    "ك": "ک",  # ARABIC KAF -> KEHEH
}

DIGIT_MAP = {
    **{chr(0x06F0 + offset): str(offset) for offset in range(10)},  # Persian
    **{chr(0x0660 + offset): str(offset) for offset in range(10)},  # Arabic-Indic
}


@dataclass(frozen=True)
class NormalizedText:
    """Normalized text plus the map back to immutable raw offsets.

    ``spans[i]`` is the raw ``[start, end)`` range that produced ``text[i]``.
    Ranges are non-decreasing, so the raw span of a normalized slice is the
    start of its first character and the end of its last.
    """

    raw: str
    text: str
    spans: tuple[tuple[int, int], ...]
    contract: NormalizationContract
    code_spans: tuple[tuple[int, int], ...]

    @property
    def contract_version(self) -> str:
        return self.contract.version

    @property
    def contract_fingerprint(self) -> str:
        return self.contract.fingerprint

    def raw_span(self, start: int, end: int) -> tuple[int, int]:
        """Translate a normalized ``[start, end)`` slice to its raw range."""

        if start < 0 or end > len(self.text) or start >= end:
            raise ValueError(f"invalid normalized span: [{start}, {end})")
        return self.spans[start][0], self.spans[end - 1][1]

    def raw_slice(self, start: int, end: int) -> str:
        """The exact raw substring behind a normalized ``[start, end)`` slice."""

        raw_start, raw_end = self.raw_span(start, end)
        return self.raw[raw_start:raw_end]

    def normalized_span(self, raw_start: int, raw_end: int) -> tuple[int, int] | None:
        """Translate a raw ``[start, end)`` range to the normalized range it produced.

        Returns ``None`` when the raw range produced no normalized characters at
        all -- a run of dropped diacritics or trailing whitespace, for example.
        That is a real answer, not an error: the caller asked about raw text
        that the contract deliberately does not represent.

        The result is the smallest normalized range covering every character
        whose raw span overlaps the request, so a raw range that lands mid-way
        through a composed cluster still yields the whole cluster rather than a
        span that slices a character in half.
        """

        if raw_start < 0 or raw_end > len(self.raw) or raw_start >= raw_end:
            raise ValueError(f"invalid raw span: [{raw_start}, {raw_end})")
        # Bisected rather than scanned. A linear scan is correct but costs
        # O(len(text)) per lookup, and callers ask once per sentence, clause,
        # and marker -- which made whole-text analysis quadratic.
        first = bisect_right(self._span_ends, raw_start)
        last = bisect_left(self._span_starts, raw_end) - 1
        if first > last:
            return None
        return first, last + 1

    @cached_property
    def _span_starts(self) -> list[int]:
        return [span[0] for span in self.spans]

    @cached_property
    def _span_ends(self) -> list[int]:
        return [span[1] for span in self.spans]

    def is_code(self, index: int) -> bool:
        return any(start <= index < end for start, end in self.code_spans)


def normalize_text(
    raw: str,
    contract: NormalizationContract = DEFAULT_NORMALIZATION_CONTRACT,
) -> str:
    """Normalized form of ``raw`` without the offset map."""

    return normalize_with_mapping(raw, contract).text


def normalize_with_mapping(
    raw: str,
    contract: NormalizationContract = DEFAULT_NORMALIZATION_CONTRACT,
) -> NormalizedText:
    """Normalize ``raw`` and keep an exact map back to its offsets."""

    state = _Emitter(contract)
    for block in _blocks(raw, contract):
        if block.kind is BlockKind.CODE:
            state.emit_verbatim(raw, block.start, block.end)
        else:
            state.emit_prose(raw, block.start, block.end)
    return state.finish(raw)


def raw_span_for_normalized_span(
    normalized: NormalizedText,
    start: int,
    end: int,
) -> tuple[int, int]:
    """Raw ``[start, end)`` evidence range behind a normalized slice."""

    return normalized.raw_span(start, end)


def normalized_span_for_raw_span(
    normalized: NormalizedText,
    raw_start: int,
    raw_end: int,
) -> tuple[int, int] | None:
    """Normalized ``[start, end)`` range produced by a raw slice, if any."""

    return normalized.normalized_span(raw_start, raw_end)


class _Emitter:
    """Builds normalized characters and their raw spans in one forward pass."""

    def __init__(self, contract: NormalizationContract) -> None:
        self.contract = contract
        self.chars: list[str] = []
        self.spans: list[tuple[int, int]] = []
        self.code_spans: list[tuple[int, int]] = []
        # A separator is held back until a following character proves it sits
        # between content. That drops leading and trailing whitespace without a
        # second pass that would have to re-align the offset map.
        self.pending: tuple[int, int] | None = None

    def emit_verbatim(self, raw: str, start: int, end: int) -> None:
        self._flush()
        begin = len(self.chars)
        for index in range(start, end):
            self.chars.append(raw[index])
            self.spans.append((index, index + 1))
        self.code_spans.append((begin, len(self.chars)))

    def emit_prose(self, raw: str, start: int, end: int) -> None:
        for cluster, cluster_start, cluster_end in _clusters(raw, start, end, self.contract):
            for char in cluster:
                replacement = _replace(char, self.contract)
                if not replacement:
                    continue
                if replacement == " ":
                    self._hold(cluster_start, cluster_end)
                    continue
                self._flush()
                for produced in replacement:
                    self.chars.append(produced)
                    self.spans.append((cluster_start, cluster_end))

    def finish(self, raw: str) -> NormalizedText:
        # A trailing pending separator is intentionally dropped.
        return NormalizedText(
            raw=raw,
            text="".join(self.chars),
            spans=tuple(self.spans),
            contract=self.contract,
            code_spans=tuple(self.code_spans),
        )

    def _hold(self, start: int, end: int) -> None:
        if not self.contract.compress_whitespace:
            self._flush()
            self.pending = (start, end)
            self._flush()
            return
        self.pending = (start, end) if self.pending is None else (self.pending[0], end)

    def _flush(self) -> None:
        pending = self.pending
        self.pending = None
        if pending is None or not self.chars:
            return
        self.chars.append(" ")
        self.spans.append(pending)


def _blocks(raw: str, contract: NormalizationContract) -> tuple[TextBlock, ...]:
    if not raw:
        return ()
    if not contract.preserve_fenced_code:
        return (TextBlock(kind=BlockKind.PROSE, start=0, end=len(raw), text=raw),)
    return scan_blocks(raw)


def _clusters(
    raw: str,
    start: int,
    end: int,
    contract: NormalizationContract,
) -> list[tuple[str, int, int]]:
    """Group each base character with its combining marks, then compose.

    Composing per cluster rather than over the whole string keeps the offset map
    exact: a cluster is one raw range no matter how many characters NFC leaves
    behind. Conjoining Hangul jamo are not combining marks and so stay
    uncomposed; the scripts this service targets are unaffected.
    """

    clusters: list[tuple[str, int, int]] = []
    index = start
    while index < end:
        stop = index + 1
        # ASCII can neither carry nor be a combining mark, so the common case
        # skips the per-character Unicode lookups entirely.
        while stop < end and not _is_ascii(raw[stop]) and unicodedata.combining(raw[stop]) != 0:
            stop += 1
        cluster = raw[index:stop]
        # A lone ASCII character is already NFC; nothing can compose with it.
        if contract.unicode_form == "NFC" and not (stop == index + 1 and _is_ascii(cluster)):
            cluster = unicodedata.normalize("NFC", cluster)
        clusters.append((cluster, index, stop))
        index = stop
    return clusters


def _is_ascii(value: str) -> bool:
    return value.isascii()


def _replace(char: str, contract: NormalizationContract) -> str:
    """Normalized replacement for one raw character; ``""`` deletes it."""

    if _is_ascii(char):
        # Fast path for the bulk of technical text: no Arabic mark, no ZWNJ, no
        # digit or letter remapping can apply, so only whitespace, control
        # characters, and case matter.
        if char.isspace():
            return " "
        if char < " " or char == "\x7f":
            return ""
        return char.lower() if contract.lowercase_letters else char
    if contract.remove_arabic_diacritics and char in ARABIC_DIACRITICS:
        return ""
    if char == TATWEEL:
        return ""
    if char == ZWNJ:
        return " " if contract.zwnj_policy is ZwnjPolicy.BOUNDARY else ""
    if char == ZWJ:
        return ""
    if char.isspace():
        return " "
    if unicodedata.category(char) in {"Cf", "Cc"}:
        # Bidi marks, byte-order marks, and stray control bytes carry no
        # lexical meaning and would otherwise split tokens invisibly.
        return ""
    if contract.unify_persian_letters and char in PERSIAN_LETTER_MAP:
        return PERSIAN_LETTER_MAP[char]
    if contract.normalize_digits and char in DIGIT_MAP:
        return DIGIT_MAP[char]
    if contract.lowercase_letters:
        return char.lower()
    return char
