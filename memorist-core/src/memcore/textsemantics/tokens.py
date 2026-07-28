"""Token boundaries and token/phrase matching.

This module exists to replace substring matching. ``"تیم" in "گرفتیم"`` is true
and wrong; ``contains_token("گرفتیم", "تیم")`` is false and right. Every match
carries the exact raw evidence span so a caller never has to persist normalized
text as evidence.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from functools import lru_cache

from memcore.textsemantics.contract import (
    DEFAULT_NORMALIZATION_CONTRACT,
    NormalizationContract,
)
from memcore.textsemantics.normalize import (
    NormalizedText,
    normalize_text,
    normalize_with_mapping,
)

# Connectors that hold a word or identifier together rather than ending a
# phrase: "api_key" and "api-key" read as the phrase "api key", and "don't"
# stays the two-token phrase "don t". Sentence punctuation is absent on
# purpose -- a full stop ends a phrase.
PHRASE_SEPARATORS = frozenset("_-'’")

# Characters that bind two tokens into one written identifier when no
# whitespace intervenes: ``api_key``, ``well-known``, ``gpt-5.4``,
# ``example.com``. The apostrophe is deliberately absent -- ``don't`` is a
# contraction, not an identifier -- even though it is a phrase separator above.
IDENTIFIER_CONNECTORS = frozenset("_-.")


@dataclass(frozen=True)
class Token:
    """One token, addressable in both normalized and raw coordinates.

    ``index`` is the position in the full token stream including fenced code, so
    it stays a reliable adjacency test even when code tokens are filtered out.

    ``key`` is the form used for comparisons. For prose it equals ``text``. For
    fenced code, whose characters are copied byte for byte, ``key`` holds the
    normalized form so a secret pasted into a fence is still detectable while
    ``text`` and the raw evidence stay exactly as written.
    """

    text: str
    key: str
    index: int
    start: int
    end: int
    raw_start: int
    raw_end: int
    in_code: bool


@dataclass(frozen=True)
class LexicalMatch:
    """A token or phrase hit, anchored to exact raw evidence."""

    text: str
    evidence: str
    raw_start: int
    raw_end: int
    token_start: int
    token_end: int


def tokenize(
    normalized: NormalizedText | str,
    language_hint: str | None = None,
    *,
    include_code: bool = False,
) -> tuple[Token, ...]:
    """Split normalized text into tokens.

    A token is a maximal run of alphanumeric characters, so punctuation, ZWNJ,
    underscores, and hyphens all separate. That makes ``api_key`` and
    ``api key`` the same two-token phrase while keeping ``tokenizer`` a single
    token that ``token`` cannot match.

    ``language_hint`` is accepted and recorded by callers for provenance;
    boundaries are script-agnostic in this contract version and the hint does
    not change them. ``include_code`` is off by default so lexical rules never
    fire on the contents of a fenced code block.
    """

    _ = language_hint
    resolved = _resolve(normalized)
    tokens: list[Token] = []
    start: int | None = None
    for index, char in enumerate(resolved.text):
        if char.isalnum():
            if start is None:
                start = index
            continue
        # A combining mark NFC could not fold into its base stays part of the
        # token it decorates, so "i" + U+0307 is one token rather than a token
        # followed by a stray separator.
        if start is not None and unicodedata.combining(char) != 0:
            continue
        if start is not None:
            tokens.append(_token(resolved, start, index, len(tokens)))
            start = None
    if start is not None:
        tokens.append(_token(resolved, start, len(resolved.text), len(tokens)))
    if include_code:
        return tuple(tokens)
    return tuple(token for token in tokens if not token.in_code)


def find_phrase(
    haystack: NormalizedText | str,
    phrase: str,
    *,
    include_code: bool = False,
) -> LexicalMatch | None:
    """First occurrence of ``phrase`` as a whole token sequence, or ``None``."""

    resolved = _resolve(haystack)
    tokens = tokenize(resolved, include_code=include_code)
    return _match_tokens(resolved, tokens, _phrase_tokens(phrase))


def contains_phrase(
    haystack: NormalizedText | str,
    phrase: str,
    *,
    include_code: bool = False,
) -> bool:
    return find_phrase(haystack, phrase, include_code=include_code) is not None


def find_token(
    haystack: NormalizedText | str,
    token: str,
    *,
    include_code: bool = False,
) -> LexicalMatch | None:
    """First occurrence of a single-token needle.

    Raises ``ValueError`` if the needle normalizes to anything other than one
    token, so a multi-word needle can never silently degrade to a partial match.
    """

    needle = _phrase_tokens(token)
    if len(needle) != 1:
        raise ValueError(f"expected a single token needle, got {len(needle)}: {token!r}")
    return find_phrase(haystack, token, include_code=include_code)


def contains_token(
    haystack: NormalizedText | str,
    token: str,
    *,
    include_code: bool = False,
) -> bool:
    return find_token(haystack, token, include_code=include_code) is not None


def find_any_phrase(
    haystack: NormalizedText | str,
    phrases: tuple[str, ...],
    *,
    include_code: bool = False,
) -> LexicalMatch | None:
    """Leftmost match across ``phrases``, tokenizing the haystack once.

    Ties at the same position resolve to the longest phrase, so ``product team``
    wins over ``team`` and the recorded evidence is the more specific one.
    """

    resolved = _resolve(haystack)
    tokens = tokenize(resolved, include_code=include_code)
    best: LexicalMatch | None = None
    for phrase in phrases:
        match = _match_tokens(resolved, tokens, _phrase_tokens(phrase))
        if match is None:
            continue
        if best is None or (match.token_start, -match.token_end) < (
            best.token_start,
            -best.token_end,
        ):
            best = match
    return best


def contains_any_phrase(
    haystack: NormalizedText | str,
    phrases: tuple[str, ...],
    *,
    include_code: bool = False,
) -> bool:
    return find_any_phrase(haystack, phrases, include_code=include_code) is not None


def find_all_phrases(
    haystack: NormalizedText | str,
    phrases: tuple[str, ...],
    *,
    include_code: bool = False,
) -> tuple[LexicalMatch, ...]:
    """Every non-overlapping match across ``phrases``, leftmost-longest.

    ``find_any_phrase`` answers "is any of these present"; this answers "where
    are all of them". Overlaps resolve the same way a lexer resolves them: the
    match starting earliest wins, and among matches starting at the same token
    the longest wins, so ``این مزیت`` is reported instead of a bare ``این``
    followed by an unrelated noun.
    """

    resolved = _resolve(haystack)
    tokens = tokenize(resolved, include_code=include_code)
    found: list[LexicalMatch] = []
    for phrase in phrases:
        needle = _phrase_tokens(phrase)
        found.extend(_match_all_tokens(resolved, tokens, needle))
    return _resolve_overlaps(found)


def identifier_phrases(
    haystack: NormalizedText | str,
    *,
    include_code: bool = True,
) -> tuple[LexicalMatch, ...]:
    """Maximal runs of tokens written as one connector-joined identifier.

    Tokenization splits ``MEMORIST_MEMORY_EXTRACTION_API_KEY`` and ``gpt-5.4``
    into their alphanumeric runs, which is what makes ``token`` unable to match
    inside ``tokenizer``. That is correct for lexical matching and lossy for
    identifiers, so the written form is recovered here as a span rather than by
    weakening the token boundaries. Single-token identifiers such as ``WSL2``
    and ``PostgreSQL`` are already intact and are not repeated here.
    """

    resolved = _resolve(haystack)
    tokens = tokenize(resolved, include_code=include_code)
    matches: list[LexicalMatch] = []
    run_start = 0
    for position in range(1, len(tokens) + 1):
        if position < len(tokens) and _connector_joined(
            resolved, tokens[position - 1], tokens[position]
        ):
            continue
        if position - run_start > 1:
            matches.append(_span_match(resolved, tokens[run_start:position]))
        run_start = position
    return tuple(matches)


def _connector_joined(resolved: NormalizedText, current: Token, following: Token) -> bool:
    """Whether two tokens are written as one identifier with no whitespace."""

    if following.index != current.index + 1 or current.in_code != following.in_code:
        return False
    gap = resolved.text[current.end : following.start]
    if not gap or len(gap) > 1:
        return False
    return gap in IDENTIFIER_CONNECTORS


def _span_match(resolved: NormalizedText, window: tuple[Token, ...]) -> LexicalMatch:
    first, last = window[0], window[-1]
    return LexicalMatch(
        text=resolved.text[first.start : last.end],
        evidence=resolved.raw[first.raw_start : last.raw_end],
        raw_start=first.raw_start,
        raw_end=last.raw_end,
        token_start=first.index,
        token_end=last.index + 1,
    )


def _match_all_tokens(
    resolved: NormalizedText,
    tokens: tuple[Token, ...],
    needle: tuple[str, ...],
) -> list[LexicalMatch]:
    width = len(needle)
    if width == 0 or width > len(tokens):
        return []
    matches: list[LexicalMatch] = []
    for offset in range(len(tokens) - width + 1):
        window = tokens[offset : offset + width]
        if tuple(token.key for token in window) != needle:
            continue
        if not _adjacent(resolved, window):
            continue
        matches.append(_span_match(resolved, window))
    return matches


def _resolve_overlaps(matches: list[LexicalMatch]) -> tuple[LexicalMatch, ...]:
    """Keep leftmost-longest matches, dropping anything they cover."""

    ordered = sorted(matches, key=lambda match: (match.token_start, -match.token_end))
    kept: list[LexicalMatch] = []
    consumed = 0
    for match in ordered:
        if match.token_start < consumed:
            continue
        kept.append(match)
        consumed = match.token_end
    return tuple(kept)


@lru_cache(maxsize=2048)
def _phrase_tokens(phrase: str) -> tuple[str, ...]:
    """Normalized token sequence for a lexicon entry.

    Needles are normalized with the same contract as haystacks, so a lexicon may
    be written with ZWNJ, Arabic yeh, or Persian digits and still match text
    written the other way.
    """

    return tuple(token.key for token in tokenize(normalize_with_mapping(phrase)))


def _match_tokens(
    resolved: NormalizedText,
    tokens: tuple[Token, ...],
    needle: tuple[str, ...],
) -> LexicalMatch | None:
    width = len(needle)
    if width == 0 or width > len(tokens):
        return None
    for offset in range(len(tokens) - width + 1):
        window = tokens[offset : offset + width]
        if tuple(token.key for token in window) != needle:
            continue
        if not _adjacent(resolved, window):
            continue
        return _span_match(resolved, window)
    return None


def _adjacent(resolved: NormalizedText, window: tuple[Token, ...]) -> bool:
    """Whether the window is one contiguous phrase in the source.

    Consecutive tokens must be separated only by whitespace or an identifier
    connector, so "api key", "api_key", and "api-key" are all the same phrase.
    Comparing token indices alone is not enough: it treats "the product. Team
    leads" as containing "product team" and lets a phrase span an empty code
    fence. Sentence punctuation ends a phrase.
    """

    for position in range(len(window) - 1):
        current, following = window[position], window[position + 1]
        if following.index != current.index + 1:
            return False
        gap = resolved.text[current.end : following.start]
        if any(char not in PHRASE_SEPARATORS and not char.isspace() for char in gap):
            return False
    return True


def _resolve(value: NormalizedText | str) -> NormalizedText:
    if isinstance(value, NormalizedText):
        return value
    return normalize_with_mapping(value)


def _token(resolved: NormalizedText, start: int, end: int, index: int) -> Token:
    raw_start, raw_end = resolved.raw_span(start, end)
    text = resolved.text[start:end]
    in_code = resolved.is_code(start)
    # A token is a run of alphanumerics, and every rule that could split or drop
    # characters keys off non-alphanumerics, so normalizing a code token yields
    # exactly one token back.
    key = normalize_text(text, resolved.contract) if in_code else text
    return Token(
        text=text,
        key=key,
        index=index,
        start=start,
        end=end,
        raw_start=raw_start,
        raw_end=raw_end,
        in_code=in_code,
    )


def normalization_contract() -> NormalizationContract:
    """The contract these token rules are defined against."""

    return DEFAULT_NORMALIZATION_CONTRACT
