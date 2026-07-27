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
    normalized_span_for_raw_span,
    raw_span_for_normalized_span,
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
from memcore.textsemantics.referential import (
    MAX_ANTECEDENT_CANDIDATES,
    REFERENTIAL_CONTRACT_VERSION,
    AntecedentCandidate,
    MarkerType,
    ReferentialConfidence,
    ReferentialMarker,
    ResolutionScope,
    detect_referential_markers,
)
from memcore.textsemantics.result import (
    TEXT_SEMANTICS_CONTRACT_VERSION,
    PhraseKind,
    PhraseSpan,
    PolarityCue,
    TextSemanticsResult,
    analyze_text,
    raw_text_hash,
)
from memcore.textsemantics.segmentation import (
    SEGMENTATION_CONTRACT_VERSION,
    ClauseKind,
    ClauseSpan,
    SentenceSpan,
    language_hints,
    segment_clauses,
    segment_sentences,
)
from memcore.textsemantics.tokens import (
    LexicalMatch,
    Token,
    contains_any_phrase,
    contains_phrase,
    contains_token,
    find_all_phrases,
    find_any_phrase,
    find_phrase,
    find_token,
    identifier_phrases,
    tokenize,
)

__all__ = [
    "ARABIC_DIACRITICS",
    "DEFAULT_NORMALIZATION_CONTRACT",
    "DIGIT_MAP",
    "MAX_ANTECEDENT_CANDIDATES",
    "NEGATION_PHRASES",
    "NORMALIZATION_CONTRACT_VERSION",
    "PERSIAN_LETTER_MAP",
    "POLARITY_CONTRACT_VERSION",
    "REFERENTIAL_CONTRACT_VERSION",
    "SEGMENTATION_CONTRACT_VERSION",
    "TEXT_SEMANTICS_CONTRACT_VERSION",
    "AntecedentCandidate",
    "BlockKind",
    "ClauseKind",
    "ClauseSpan",
    "LexicalMatch",
    "Lexicon",
    "MarkerType",
    "NormalizationContract",
    "NormalizedText",
    "PhraseKind",
    "PhraseSpan",
    "Polarity",
    "PolarityCue",
    "PolarityResult",
    "ReferentialConfidence",
    "ReferentialMarker",
    "ResolutionScope",
    "SentenceSpan",
    "TextBlock",
    "TextSemanticsResult",
    "Token",
    "ZwnjPolicy",
    "analyze_text",
    "code_ranges",
    "coerce_polarity",
    "contains_any_phrase",
    "contains_phrase",
    "contains_token",
    "detect_referential_markers",
    "extract_polarity",
    "find_all_phrases",
    "find_any_phrase",
    "find_phrase",
    "find_token",
    "identifier_phrases",
    "is_hypothetical",
    "language_hints",
    "normalize_text",
    "normalize_with_mapping",
    "normalized_span_for_raw_span",
    "polarity_from_flag",
    "raw_span_for_normalized_span",
    "raw_text_hash",
    "scan_blocks",
    "segment_clauses",
    "segment_sentences",
    "tokenize",
]
