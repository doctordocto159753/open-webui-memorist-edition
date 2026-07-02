from __future__ import annotations

import re
from hashlib import sha256
from uuid import NAMESPACE_URL, uuid5

from memcore.memory_worker.segmentation.models import (
    SegmentationConfidence,
    SentenceSegmentation,
    SentenceUnit,
)
from memcore.memory_worker.segmentation.rules import (
    ABBREVIATIONS,
    CONJOINING_HEADING_MAX_CHARS,
    FENCE_RE,
    HEADING_LABEL_RE,
    LIST_ITEM_RE,
    MARKDOWN_HEADING_RE,
    QUOTE_LINE_RE,
    SENTENCE_PUNCTUATION,
    URL_RE,
)
from memcore.models import TextUnit, TextUnitType


class SentenceSegmenter:
    """Deterministic first-class sentence segmenter for Jakobson analysis."""

    def segment(self, message_uuid: str, text: str) -> SentenceSegmentation:
        if not text or not text.strip():
            return SentenceSegmentation(message_uuid=message_uuid, units=[])

        raw_spans = self._line_groups(text)
        sentence_spans: list[tuple[int, str, SegmentationConfidence, str | None]] = []
        for start, chunk, confidence, notes, allow_sentence_split in raw_spans:
            if allow_sentence_split:
                split_spans = _split_sentences(chunk, start)
                if len(split_spans) > 1:
                    sentence_spans.extend(
                        (item_start, item_text, confidence, notes)
                        for item_start, item_text in split_spans
                    )
                else:
                    sentence_spans.append((start, chunk, confidence, notes))
            else:
                sentence_spans.append((start, chunk, confidence, notes))

        units = [
            _unit(message_uuid, index + 1, item_start, item_text, confidence, notes)
            for index, (item_start, item_text, confidence, notes) in enumerate(sentence_spans)
            if item_text.strip()
        ]
        return SentenceSegmentation(message_uuid=message_uuid, units=units)

    def to_text_units(
        self,
        message_uuid: str,
        session_uuid: str,
        speaker_role: str,
        text: str,
    ) -> list[TextUnit]:
        return [
            TextUnit(
                text_unit_uuid=unit.unit_uuid,
                unit_uuid=unit.unit_uuid,
                message_uuid=message_uuid,
                session_uuid=session_uuid,
                unit_index=unit.sentence_index - 1,
                unit_type=TextUnitType.SENTENCE,
                text=unit.text,
                start_char=unit.char_start,
                end_char=unit.char_end,
                char_start=unit.char_start,
                char_end=unit.char_end,
                language_code=unit.language_hint,
                language_hint=unit.language_hint,
                speaker_role=speaker_role,
                content_hash=unit.content_hash,
                segmentation_confidence=unit.segmentation_confidence,
                segmentation_notes=unit.segmentation_notes,
            )
            for unit in self.segment(message_uuid, text).units
        ]

    def _line_groups(
        self,
        text: str,
    ) -> list[tuple[int, str, SegmentationConfidence, str | None, bool]]:
        lines = text.splitlines(keepends=True)
        indexed_lines: list[tuple[int, str]] = []
        cursor = 0
        for line in lines:
            indexed_lines.append((cursor, line))
            cursor += len(line)

        groups: list[tuple[int, str, SegmentationConfidence, str | None, bool]] = []
        index = 0
        while index < len(indexed_lines):
            start, line = indexed_lines[index]
            if not line.strip():
                index += 1
                continue

            if FENCE_RE.match(line):
                block_start = start
                block = line
                index += 1
                while index < len(indexed_lines):
                    _, next_line = indexed_lines[index]
                    block += next_line
                    index += 1
                    if FENCE_RE.match(next_line):
                        break
                groups.append((block_start, block, "low", "technical code block", False))
                continue

            if LIST_ITEM_RE.match(line):
                item_start = start
                item = line
                index += 1
                while index < len(indexed_lines):
                    _, next_line = indexed_lines[index]
                    if (
                        not next_line.strip()
                        or LIST_ITEM_RE.match(next_line)
                        or FENCE_RE.match(next_line)
                        or _is_heading(next_line)
                        or QUOTE_LINE_RE.match(next_line)
                    ):
                        break
                    item += next_line
                    index += 1
                groups.append(
                    (item_start, item, "high", "list item treated as sentence unit", True)
                )
                continue

            if _is_heading(line) and index + 1 < len(indexed_lines):
                next_start, next_line = indexed_lines[index + 1]
                if _heading_should_join(line, next_line):
                    groups.append(
                        (
                            start,
                            line + next_line,
                            "medium",
                            "heading joined with following sentence",
                            True,
                        )
                    )
                    index += 2
                    continue
                groups.append((start, line, "medium", "standalone heading", False))
                index += 1
                continue

            if QUOTE_LINE_RE.match(line):
                quote_start = start
                quote = line
                index += 1
                while index < len(indexed_lines) and QUOTE_LINE_RE.match(indexed_lines[index][1]):
                    quote += indexed_lines[index][1]
                    index += 1
                groups.append((quote_start, quote, "medium", "quoted example preserved", False))
                continue

            paragraph_start = start
            paragraph = line
            index += 1
            while index < len(indexed_lines):
                _, next_line = indexed_lines[index]
                if not next_line.strip():
                    break
                if (
                    FENCE_RE.match(next_line)
                    or LIST_ITEM_RE.match(next_line)
                    or _is_heading(next_line)
                    or QUOTE_LINE_RE.match(next_line)
                ):
                    break
                paragraph += next_line
                index += 1
            groups.append((paragraph_start, paragraph, _confidence(paragraph), None, True))
        return groups


def _unit(
    message_uuid: str,
    sentence_index: int,
    start: int,
    text: str,
    confidence: SegmentationConfidence,
    notes: str | None,
) -> SentenceUnit:
    content_hash = sha256(text.encode("utf-8")).hexdigest()
    unit_uuid = str(
        uuid5(NAMESPACE_URL, f"memorist:{message_uuid}:{sentence_index}:{content_hash}")
    )
    return SentenceUnit(
        unit_uuid=unit_uuid,
        sentence_index=sentence_index,
        text=text,
        char_start=start,
        char_end=start + len(text),
        content_hash=content_hash,
        language_hint=_detect_language(text),
        segmentation_confidence=confidence,
        segmentation_notes=notes,
    )


def _split_sentences(text: str, absolute_start: int) -> list[tuple[int, str]]:
    spans: list[tuple[int, str]] = []
    start = 0
    quote_stack: list[str] = []
    index = 0
    while index < len(text):
        char = text[index]
        _update_quote_stack(quote_stack, char)
        if not quote_stack and char in SENTENCE_PUNCTUATION and _can_split_at(text, index):
            end = _consume_closing_quotes(text, index + 1)
            spans.append((absolute_start + start, text[start:end]))
            start = end
        index += 1
    if start < len(text):
        spans.append((absolute_start + start, text[start:]))
    return [span for span in spans if span[1].strip()]


def _can_split_at(text: str, index: int) -> bool:
    char = text[index]
    before = text[max(0, index - 16) : index + 1]
    after = text[index + 1 : index + 16]
    if char == ".":
        if (
            index > 0
            and index + 1 < len(text)
            and text[index - 1].isdigit()
            and (text[index + 1].isdigit() or _is_list_marker_period(text, index))
        ):
            return False
        token = before.strip().split()[-1].lower().rstrip(".") if before.strip().split() else ""
        if token in ABBREVIATIONS or URL_RE.search(before + after):
            return False
    if index + 1 == len(text):
        return True
    next_char = text[index + 1]
    return next_char.isspace() or next_char in {'"', "'", "”", "»"}


def _is_list_marker_period(text: str, index: int) -> bool:
    prefix = text[: index + 1]
    return re.search(r"(?:^|\n)\s*[0-9۰-۹٠-٩]+\.$", prefix) is not None


def _consume_closing_quotes(text: str, index: int) -> int:
    while index < len(text) and text[index] in {'"', "'", "”", "»"}:
        index += 1
    return index


def _update_quote_stack(stack: list[str], char: str) -> None:
    pairs = {"“": "”", "«": "»"}
    if char in pairs:
        stack.append(pairs[char])
    elif stack and char == stack[-1]:
        stack.pop()
    elif char == '"':
        if stack and stack[-1] == '"':
            stack.pop()
        else:
            stack.append('"')


def _is_heading(line: str) -> bool:
    return MARKDOWN_HEADING_RE.match(line) is not None or HEADING_LABEL_RE.match(line) is not None


def _heading_should_join(heading: str, next_line: str) -> bool:
    if not next_line.strip():
        return False
    if LIST_ITEM_RE.match(next_line) or FENCE_RE.match(next_line) or QUOTE_LINE_RE.match(next_line):
        return False
    return len(heading.strip()) <= CONJOINING_HEADING_MAX_CHARS


def _confidence(text: str) -> SegmentationConfidence:
    if any(char in text for char in SENTENCE_PUNCTUATION):
        return "high"
    if "\n" in text.strip():
        return "medium"
    return "low"


def _detect_language(text: str) -> str:
    has_persian = re.search(r"[\u0600-\u06ff]", text) is not None
    has_latin = re.search(r"[A-Za-z]", text) is not None
    if has_persian and has_latin:
        return "mixed"
    if has_persian:
        return "fa"
    if has_latin:
        return "en"
    return "unknown"
