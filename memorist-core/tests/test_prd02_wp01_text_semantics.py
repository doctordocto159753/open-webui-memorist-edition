"""PRD-02 WP01: the shared text-semantics foundation.

Covers the normalization contract rule by rule, the normalized-to-raw offset
map, fenced-code byte preservation, token/phrase matching, and polarity.
"""

from __future__ import annotations

import random
import unicodedata

import pytest

from memcore.textsemantics import (
    DEFAULT_NORMALIZATION_CONTRACT,
    NORMALIZATION_CONTRACT_VERSION,
    BlockKind,
    NormalizationContract,
    Polarity,
    ZwnjPolicy,
    coerce_polarity,
    contains_phrase,
    contains_token,
    extract_polarity,
    find_any_phrase,
    find_phrase,
    find_token,
    is_hypothetical,
    normalize_text,
    normalize_with_mapping,
    polarity_from_flag,
    scan_blocks,
    tokenize,
)

ZWNJ = "‌"


# --------------------------------------------------------------------------
# Normalization rules
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # Unicode NFC composition
        ("é", "é"),
        ("Å", "å"),
        # Persian/Arabic letter unification
        ("ي", "ی"),
        ("ى", "ی"),
        ("ك", "ک"),
        ("كتاب", "کتاب"),
        # Arabic diacritic removal
        ("کِتاب", "کتاب"),
        ("حتماً", "حتما"),
        # Tatweel removal
        ("مــن", "من"),
        # ZWNJ becomes a boundary
        (f"توسعه{ZWNJ}دهنده", "توسعه دهنده"),
        # Digits
        ("۱۲۳", "123"),
        ("١٢٣", "123"),
        ("۱۲۳ABC", "123abc"),
        # Latin case
        ("MiXeD Case", "mixed case"),
        # Whitespace compression and trimming
        ("  a   b  ", "a b"),
        ("a\t\n b", "a b"),
        (" a ", "a"),
        # Zero-width and bidi controls carry no lexical meaning
        ("a‍b", "ab"),
        ("‏سلام‎", "سلام"),
        # Empty input
        ("", ""),
        ("   ", ""),
    ],
)
def test_normalization_rules(raw: str, expected: str) -> None:
    assert normalize_text(raw) == expected


def test_latin_diacritics_are_never_deleted() -> None:
    """Diacritic removal is scoped to the Arabic blocks by construction.

    The two literals below are deliberately different byte sequences: the first
    is decomposed (base plus combining acute), the second precomposed. NFC must
    fold the first into the second, and neither acute may be deleted the way an
    Arabic harakat is.
    """

    decomposed = "résumé"
    composed = "résumé"

    assert normalize_text(decomposed) == composed
    assert normalize_text(composed) == composed
    assert normalize_text("Müller") == "müller"
    assert normalize_text("MÜLLER") == "müller"


def test_contract_options_are_honoured() -> None:
    remove = NormalizationContract(zwnj_policy=ZwnjPolicy.REMOVE)
    assert normalize_text(f"توسعه{ZWNJ}دهنده", remove) == "توسعهدهنده"

    keep_case = NormalizationContract(lowercase_letters=False)
    assert normalize_text("ABC", keep_case) == "ABC"

    keep_digits = NormalizationContract(normalize_digits=False)
    assert normalize_text("۱۲۳", keep_digits) == "۱۲۳"

    keep_marks = NormalizationContract(remove_arabic_diacritics=False)
    assert normalize_text("کِ", keep_marks) == "کِ"


def test_contract_version_and_fingerprint_are_stable_and_sensitive() -> None:
    baseline = DEFAULT_NORMALIZATION_CONTRACT
    assert baseline.version == NORMALIZATION_CONTRACT_VERSION
    assert baseline.fingerprint == NormalizationContract().fingerprint

    changed = NormalizationContract(remove_arabic_diacritics=False)
    assert changed.fingerprint != baseline.fingerprint, (
        "a rule change must change the fingerprint so downstream content "
        "fingerprints can be invalidated"
    )


# --------------------------------------------------------------------------
# Offset mapping
# --------------------------------------------------------------------------


def test_offset_map_recovers_exact_raw_evidence() -> None:
    raw = "  کِتاب" + ZWNJ + "های   ۱۲۳ "
    normalized = normalize_with_mapping(raw)
    tokens = tokenize(normalized)

    assert [token.text for token in tokens] == ["کتاب", "های", "123"]
    assert raw[tokens[0].raw_start : tokens[0].raw_end] == "کِتاب"
    assert raw[tokens[2].raw_start : tokens[2].raw_end] == "۱۲۳"


def test_raw_slice_matches_direct_indexing_for_every_token() -> None:
    raw = "Team: تیم‌های ۱۲۳ — resume/résumé api_key"
    normalized = normalize_with_mapping(raw)
    for token in tokenize(normalized):
        assert normalized.raw_slice(token.start, token.end) == raw[token.raw_start : token.raw_end]
        assert normalized.text[token.start : token.end] == token.text


def test_raw_span_rejects_invalid_slices() -> None:
    normalized = normalize_with_mapping("abc")
    with pytest.raises(ValueError):
        normalized.raw_span(2, 2)
    with pytest.raises(ValueError):
        normalized.raw_span(0, 99)


ALPHABET = f"ابپتثجچحخدذرزژسشصضطظعغفقکگلمنوهیيىكًَِّْٰabcXYZé۰۱۲٣٤0129{ZWNJ}‍‏﻿ \t\n ._-/:!?،؟«»()"


def _random_texts(count: int, seed: int = 20260726) -> list[str]:
    rng = random.Random(seed)
    return ["".join(rng.choice(ALPHABET) for _ in range(rng.randint(0, 60))) for _ in range(count)]


def test_property_every_token_span_reconstructs_its_raw_substring() -> None:
    """The mapping must be exact for arbitrary mixed-script input."""

    for raw in _random_texts(400):
        normalized = normalize_with_mapping(raw)
        for token in tokenize(normalized):
            evidence = raw[token.raw_start : token.raw_end]
            assert evidence == normalized.raw_slice(token.start, token.end)
            # Re-normalizing the exact raw evidence reproduces the token, which
            # is what makes a stored raw span auditable against the decision
            # that was made on the normalized form.
            assert normalize_text(evidence) == token.text


def test_property_spans_are_ordered_and_within_bounds() -> None:
    """Spans advance monotonically, which is what makes ``raw_span`` sound.

    Consecutive spans may repeat: one cluster can emit several characters (an
    uncomposed combining mark, or a case mapping that lengthens), and they all
    point at the same raw range.
    """

    for raw in _random_texts(400, seed=7):
        normalized = normalize_with_mapping(raw)
        assert len(normalized.spans) == len(normalized.text)
        previous_start, previous_end = 0, 0
        for start, end in normalized.spans:
            assert 0 <= start < end <= len(raw)
            assert start >= previous_start
            assert end >= previous_end
            previous_start, previous_end = start, end


def test_property_normalization_is_idempotent() -> None:
    for raw in _random_texts(300, seed=99):
        once = normalize_text(raw)
        assert normalize_text(once) == once


# --------------------------------------------------------------------------
# Fenced code
# --------------------------------------------------------------------------


def test_scan_blocks_splits_prose_and_code() -> None:
    raw = "before\n```py\nx = 1\n```\nafter"
    blocks = scan_blocks(raw)
    kinds = [block.kind for block in blocks]

    assert kinds == [BlockKind.PROSE, BlockKind.CODE, BlockKind.PROSE]
    assert blocks[1].text == "```py\nx = 1\n```\n"
    assert "".join(block.text for block in blocks) == raw


def test_unclosed_fence_runs_to_end_of_text() -> None:
    raw = "intro\n```\nnever closed"
    blocks = scan_blocks(raw)

    assert [block.kind for block in blocks] == [BlockKind.PROSE, BlockKind.CODE]
    assert blocks[1].text == "```\nnever closed"


def test_tilde_fences_and_longer_fences_are_recognised() -> None:
    raw = "a\n~~~~\nkept\n~~~~\nb"
    blocks = scan_blocks(raw)
    assert [block.kind for block in blocks] == [BlockKind.PROSE, BlockKind.CODE, BlockKind.PROSE]


def test_fenced_code_bytes_are_preserved_exactly() -> None:
    code = "```python\nKEY = 'ي  ك ۱۲'   # KEEP  ME\nAPI_KEY = 'sk-x'\n```\n"
    raw = f"Please review ی this.\n{code}Done تیم."
    normalized = normalize_with_mapping(raw)

    preserved = "".join(normalized.text[start:end] for start, end in normalized.code_spans)
    assert preserved == code, "normalization must not touch a single byte of fenced code"
    assert "ي" in normalized.text and "ك" in normalized.text and "۱۲" in normalized.text
    # Prose around it is still normalized.
    assert "please review ی this." in normalized.text


def test_lexical_rules_ignore_code_but_can_opt_in() -> None:
    raw = "Nothing here.\n```\nteam = 1\n```\n"
    normalized = normalize_with_mapping(raw)

    assert contains_token(normalized, "team") is False
    assert contains_token(normalized, "team", include_code=True) is True


def test_code_matching_is_case_insensitive_without_rewriting_bytes() -> None:
    raw = "```\nPASSWORD = 'x'\n```\n"
    normalized = normalize_with_mapping(raw)

    assert contains_token(normalized, "password", include_code=True) is True
    assert "PASSWORD" in normalized.text


def test_phrase_cannot_match_across_a_code_fence() -> None:
    raw = "product\n```\nnoise\n```\nteam"
    normalized = normalize_with_mapping(raw)

    assert contains_phrase(normalized, "product team") is False


# --------------------------------------------------------------------------
# Tokens and matching
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("api_key rotation", ["api", "key", "rotation"]),
        ("kebab-case-name", ["kebab", "case", "name"]),
        ("module.path.v1", ["module", "path", "v1"]),
        ("don't", ["don", "t"]),
        ("v1.2.3", ["v1", "2", "3"]),
        ("https://example.com/x", ["https", "example", "com", "x"]),
        ("تیم‌های ما", ["تیم", "های", "ما"]),
        ("سلام!", ["سلام"]),
        ("", []),
        ("   ", []),
        ("...", []),
    ],
)
def test_token_boundaries(raw: str, expected: list[str]) -> None:
    assert [token.text for token in tokenize(raw)] == expected


def test_find_token_rejects_multi_token_needles() -> None:
    with pytest.raises(ValueError):
        find_token("the product team", "product team")


def test_find_phrase_reports_exact_raw_evidence() -> None:
    raw = "The Product  Team must ship."
    match = find_phrase(raw, "product team")

    assert match is not None
    assert match.evidence == "Product  Team"
    assert raw[match.raw_start : match.raw_end] == "Product  Team"


def test_find_any_phrase_prefers_leftmost_then_longest() -> None:
    match = find_any_phrase("the product team ships", ("team", "product team"))

    assert match is not None
    assert match.evidence == "product team"


def test_needles_normalize_the_same_way_as_haystacks() -> None:
    # Needle written with Arabic yeh, haystack with Farsi yeh.
    assert contains_token("تیم ما", "تيم") is True
    # Needle written with ZWNJ, haystack with a plain space.
    assert contains_phrase("توسعه دهنده ارشد", f"توسعه{ZWNJ}دهنده") is True
    # Persian digits either side.
    assert contains_token("نسخه ۱۲۳", "123") is True


# --------------------------------------------------------------------------
# Polarity
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("We deploy on Friday.", Polarity.AFFIRMED),
        ("We never deploy on Friday.", Polarity.NEGATED),
        ("We do not deploy on Friday.", Polarity.NEGATED),
        ("We don't deploy on Friday.", Polarity.NEGATED),
        ("We dont deploy on Friday.", Polarity.NEGATED),
        ("There is no deadline.", Polarity.NEGATED),
        ("ما همیشه از Jira استفاده می‌کنیم.", Polarity.AFFIRMED),
        ("ما هرگز از Jira استفاده نمی‌کنیم.", Polarity.NEGATED),
        ("این کار را نباید انجام دهیم.", Polarity.NEGATED),
        ("ما از Jira استفاده نمی‌کنیم.", Polarity.NEGATED),
        ("ما از Jira استفاده نمیکنیم.", Polarity.NEGATED),
        ("", Polarity.UNKNOWN),
        ("   ", Polarity.UNKNOWN),
        ("```\ncode only\n```", Polarity.UNKNOWN),
    ],
)
def test_polarity_extraction(text: str, expected: Polarity) -> None:
    assert extract_polarity(text).polarity is expected


def test_polarity_does_not_fire_on_substrings() -> None:
    """The old substring markers fired inside ordinary words."""

    assert extract_polarity("Nevertheless we ship.").polarity is Polarity.AFFIRMED
    assert extract_polarity("Send a notification.").polarity is Polarity.AFFIRMED
    assert extract_polarity("The nonce is stored.").polarity is Polarity.AFFIRMED
    # A bound negation prefix still needs to be a prefix of something.
    assert extract_polarity("Rainy nights").polarity is Polarity.AFFIRMED


def test_polarity_carries_exact_raw_evidence() -> None:
    raw = "We  NEVER  deploy on Friday."
    result = extract_polarity(raw)

    assert result.polarity is Polarity.NEGATED
    assert result.marker == "never"
    assert result.evidence == "NEVER"
    assert raw[result.raw_start or 0 : result.raw_end or 0] == "NEVER"


def test_hypothetical_detection_is_token_aware() -> None:
    assert is_hypothetical("If we ship, we celebrate.") is True
    assert is_hypothetical("فرض کنیم که این درست است.") is True
    assert is_hypothetical("The gift was a big deal.") is False


def test_polarity_from_flag_and_coercion() -> None:
    assert polarity_from_flag(None) is Polarity.UNKNOWN
    assert polarity_from_flag(True) is Polarity.NEGATED
    assert polarity_from_flag(False) is Polarity.AFFIRMED

    assert coerce_polarity("negated") is Polarity.NEGATED
    assert coerce_polarity(Polarity.AFFIRMED) is Polarity.AFFIRMED
    assert coerce_polarity("nonsense") is Polarity.UNKNOWN
    assert coerce_polarity(None) is Polarity.UNKNOWN


def test_arabic_diacritics_are_derived_from_unicode_data() -> None:
    from memcore.textsemantics import ARABIC_DIACRITICS

    assert "ِ" in ARABIC_DIACRITICS
    assert "ٰ" in ARABIC_DIACRITICS
    assert all(unicodedata.category(char) == "Mn" for char in ARABIC_DIACRITICS)
    assert "́" not in ARABIC_DIACRITICS, "Latin combining acute must never be dropped"
