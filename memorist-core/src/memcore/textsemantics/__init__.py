"""Shared deterministic text-semantics service.

One normalization, tokenization, matching, and polarity contract for Persian,
Arabic-script Persian, English, and mixed technical text. Lite and Full call
these same pure functions, so both runtimes reach the same semantic decision.

The service is pure: no persistence, no I/O, no configuration lookups.
"""

from memcore.textsemantics.blocks import BlockKind, TextBlock, code_ranges, scan_blocks
from memcore.textsemantics.contract import (
    DEFAULT_NORMALIZATION_CONTRACT,
    NORMALIZATION_CONTRACT_VERSION,
    NormalizationContract,
    ZwnjPolicy,
)
from memcore.textsemantics.lexicon import Lexicon
from memcore.textsemantics.normalize import (
    ARABIC_DIACRITICS,
    DIGIT_MAP,
    PERSIAN_LETTER_MAP,
    NormalizedText,
    normalize_text,
    normalize_with_mapping,
)
from memcore.textsemantics.polarity import (
    NEGATION_PHRASES,
    POLARITY_CONTRACT_VERSION,
    Polarity,
    PolarityResult,
    coerce_polarity,
    extract_polarity,
    is_hypothetical,
    polarity_from_flag,
)
from memcore.textsemantics.tokens import (
    LexicalMatch,
    Token,
    contains_any_phrase,
    contains_phrase,
    contains_token,
    find_any_phrase,
    find_phrase,
    find_token,
    tokenize,
)

__all__ = [
    "ARABIC_DIACRITICS",
    "DEFAULT_NORMALIZATION_CONTRACT",
    "DIGIT_MAP",
    "NEGATION_PHRASES",
    "NORMALIZATION_CONTRACT_VERSION",
    "PERSIAN_LETTER_MAP",
    "POLARITY_CONTRACT_VERSION",
    "BlockKind",
    "LexicalMatch",
    "Lexicon",
    "NormalizationContract",
    "NormalizedText",
    "Polarity",
    "PolarityResult",
    "TextBlock",
    "Token",
    "ZwnjPolicy",
    "code_ranges",
    "coerce_polarity",
    "contains_any_phrase",
    "contains_phrase",
    "contains_token",
    "extract_polarity",
    "find_any_phrase",
    "find_phrase",
    "find_token",
    "is_hypothetical",
    "normalize_text",
    "normalize_with_mapping",
    "polarity_from_flag",
    "scan_blocks",
    "tokenize",
]
