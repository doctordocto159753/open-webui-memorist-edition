"""Deterministic block scanner separating prose from fenced code.

Normalization must never rewrite bytes inside a fenced code block: an operator
pasting ``KEY=ABC`` or a Persian identifier into a fence expects the stored
evidence to survive byte for byte. The scanner marks the ranges; normalization
copies them verbatim.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from enum import StrEnum

MAX_FENCE_INDENT = 3


class BlockKind(StrEnum):
    PROSE = "prose"
    CODE = "code"


@dataclass(frozen=True)
class TextBlock:
    """A half-open ``[start, end)`` range of the raw text."""

    kind: BlockKind
    start: int
    end: int
    text: str


def scan_blocks(raw: str) -> tuple[TextBlock, ...]:
    """Split ``raw`` into alternating prose and fenced-code blocks.

    A fence opens on a line whose first non-blank content is three or more
    backticks or tildes, indented at most three spaces. It closes on the next
    line that repeats the same fence character at least as many times with no
    trailing content. An unclosed fence runs to the end of the text, matching
    how Markdown renderers treat a truncated block. Fence lines belong to the
    code block so the block round-trips exactly.
    """

    if not raw:
        return ()

    blocks: list[TextBlock] = []
    prose_start = 0
    open_fence: tuple[str, int] | None = None
    code_start = 0

    for line, start, end in _lines(raw):
        fence = _fence_token(line)
        if open_fence is None:
            if fence is None:
                continue
            if prose_start < start:
                blocks.append(_block(BlockKind.PROSE, raw, prose_start, start))
            code_start = start
            open_fence = fence
            continue
        char, length = open_fence
        if fence is not None and fence[0] == char and fence[1] >= length and _is_bare_fence(line):
            blocks.append(_block(BlockKind.CODE, raw, code_start, end))
            prose_start = end
            open_fence = None

    if open_fence is not None:
        blocks.append(_block(BlockKind.CODE, raw, code_start, len(raw)))
    elif prose_start < len(raw):
        blocks.append(_block(BlockKind.PROSE, raw, prose_start, len(raw)))
    return tuple(blocks)


def code_ranges(raw: str) -> tuple[tuple[int, int], ...]:
    """Raw ``[start, end)`` ranges covered by fenced code."""

    return tuple(
        (block.start, block.end) for block in scan_blocks(raw) if block.kind is BlockKind.CODE
    )


def _block(kind: BlockKind, raw: str, start: int, end: int) -> TextBlock:
    return TextBlock(kind=kind, start=start, end=end, text=raw[start:end])


def _lines(raw: str) -> Iterator[tuple[str, int, int]]:
    """Yield ``(line_without_newline, start, end_including_newline)``."""

    position = 0
    length = len(raw)
    while position < length:
        break_at = raw.find("\n", position)
        if break_at == -1:
            yield raw[position:length], position, length
            return
        yield raw[position:break_at], position, break_at + 1
        position = break_at + 1


def _fence_token(line: str) -> tuple[str, int] | None:
    stripped = line.lstrip(" \t")
    if len(line) - len(stripped) > MAX_FENCE_INDENT:
        return None
    for char in ("`", "~"):
        if stripped.startswith(char * 3):
            run = len(stripped) - len(stripped.lstrip(char))
            return char, run
    return None


def _is_bare_fence(line: str) -> bool:
    """True when the line holds only fence characters and whitespace.

    A closing fence carries no info string, so ```` ```python ```` opens a block
    and a bare ```` ``` ```` closes it.
    """

    stripped = line.strip()
    if not stripped:
        return False
    char = stripped[0]
    return char in "`~" and stripped == char * len(stripped)
