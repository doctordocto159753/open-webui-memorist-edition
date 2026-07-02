import re

from memcore.memory_worker.contracts import UnitizedSpan
from memcore.models import TextUnit, TextUnitType
from memcore.repositories.memory_worker import canonical_text_hash

LIST_ITEM = re.compile(r"^\s*(?:[-*+•]|[0-9۰-۹]+[.)])\s+")
HEADING = re.compile(r"^\s{0,3}#{1,6}\s+\S")
QUOTE = re.compile(r"^\s*>")
FENCE = re.compile(r"^\s*```")
URL_NEARBY = re.compile(r"https?://|www\.")
ABBREVIATIONS = {"mr", "mrs", "ms", "dr", "prof", "sr", "jr", "e.g", "i.e", "etc"}
SENTENCE_PUNCTUATION = {".", "!", "?", "؟", "۔"}


class DeterministicUnitizer:
    def unitize(self, text: str) -> list[UnitizedSpan]:
        if not text:
            return []

        lines = text.splitlines(keepends=True)
        spans: list[tuple[int, str]] = []
        cursor = 0
        for line in lines:
            spans.append((cursor, line))
            cursor += len(line)

        units: list[UnitizedSpan] = []
        index = 0
        line_index = 0
        while line_index < len(spans):
            start, line = spans[line_index]
            if not line.strip():
                line_index += 1
                continue

            if FENCE.match(line):
                block_start = start
                block_text = line
                line_index += 1
                while line_index < len(spans):
                    _, next_line = spans[line_index]
                    block_text += next_line
                    line_index += 1
                    if FENCE.match(next_line):
                        break
                index = _append_unit(units, index, TextUnitType.CODE_BLOCK, block_text, block_start)
                continue

            if LIST_ITEM.match(line):
                item_text = line
                line_index += 1
                while line_index < len(spans):
                    _, next_line = spans[line_index]
                    if (
                        not next_line.strip()
                        or LIST_ITEM.match(next_line)
                        or FENCE.match(next_line)
                    ):
                        break
                    if HEADING.match(next_line) or QUOTE.match(next_line):
                        break
                    item_text += next_line
                    line_index += 1
                index = _append_unit(units, index, TextUnitType.LIST_ITEM, item_text, start)
                continue

            if HEADING.match(line):
                index = _append_unit(units, index, TextUnitType.HEADING, line, start)
                line_index += 1
                continue

            if QUOTE.match(line):
                quote_text = line
                line_index += 1
                while line_index < len(spans) and QUOTE.match(spans[line_index][1]):
                    quote_text += spans[line_index][1]
                    line_index += 1
                index = _append_unit(units, index, TextUnitType.QUOTE, quote_text, start)
                continue

            paragraph_start = start
            paragraph_text = line
            line_index += 1
            while line_index < len(spans):
                _, next_line = spans[line_index]
                if not next_line.strip():
                    break
                if FENCE.match(next_line) or LIST_ITEM.match(next_line) or HEADING.match(next_line):
                    break
                if QUOTE.match(next_line):
                    break
                paragraph_text += next_line
                line_index += 1

            sentence_spans = _split_sentences(paragraph_text, paragraph_start)
            if len(sentence_spans) == 1 and "\n" in paragraph_text:
                index = _append_unit(
                    units, index, TextUnitType.PARAGRAPH, paragraph_text, paragraph_start
                )
            else:
                for sentence_start, sentence_text in sentence_spans:
                    unit_type = (
                        TextUnitType.SENTENCE
                        if _has_sentence_signal(sentence_text)
                        else TextUnitType.FRAGMENT
                    )
                    index = _append_unit(units, index, unit_type, sentence_text, sentence_start)

        return units

    def to_text_units(
        self,
        message_uuid: str,
        session_uuid: str,
        speaker_role: str,
        text: str,
    ) -> list[TextUnit]:
        return [
            TextUnit(
                message_uuid=message_uuid,
                session_uuid=session_uuid,
                unit_index=span.unit_index,
                unit_type=span.unit_type,
                text=span.text,
                start_char=span.start_char,
                end_char=span.end_char,
                language_code=span.language_code,
                speaker_role=speaker_role,
                content_hash=canonical_text_hash(span.text),
            )
            for span in self.unitize(text)
        ]


def _append_unit(
    units: list[UnitizedSpan],
    index: int,
    unit_type: TextUnitType,
    text: str,
    start_char: int,
) -> int:
    if not text:
        return index
    units.append(
        UnitizedSpan(
            unit_index=index,
            unit_type=unit_type,
            text=text,
            start_char=start_char,
            end_char=start_char + len(text),
            language_code=_detect_language(text),
        )
    )
    return index + 1


def _split_sentences(text: str, absolute_start: int) -> list[tuple[int, str]]:
    spans: list[tuple[int, str]] = []
    start = 0
    index = 0
    while index < len(text):
        char = text[index]
        if char in SENTENCE_PUNCTUATION and _can_split_at(text, index):
            end = index + 1
            spans.append((absolute_start + start, text[start:end]))
            start = end
        index += 1
    if start < len(text):
        spans.append((absolute_start + start, text[start:]))
    return [span for span in spans if span[1].strip()]


def _can_split_at(text: str, index: int) -> bool:
    char = text[index]
    before = text[max(0, index - 12) : index + 1]
    after = text[index + 1 : index + 12]
    if char == ".":
        if (
            index > 0
            and index + 1 < len(text)
            and text[index - 1].isdigit()
            and text[index + 1].isdigit()
        ):
            return False
        token = before.strip().split()[-1].lower().rstrip(".") if before.strip().split() else ""
        if token in ABBREVIATIONS:
            return False
        if URL_NEARBY.search(before + after):
            return False
    return index + 1 == len(text) or text[index + 1].isspace()


def _has_sentence_signal(text: str) -> bool:
    return any(char in text for char in SENTENCE_PUNCTUATION)


def _detect_language(text: str) -> str:
    if re.search(r"[\u0600-\u06ff]", text):
        return "fa"
    return "en"
