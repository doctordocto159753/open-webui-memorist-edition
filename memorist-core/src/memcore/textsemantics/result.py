"""The deterministic envelope a model's semantic analysis is validated against.

This is a **technical** container, not an analysis. It records what can be
established without interpreting language:

* exact, immutable raw text and its hash;
* the normalized comparison view and a bidirectional offset map;
* fenced-code ranges, which must survive byte for byte;
* spans delimited by the writer's own punctuation;
* tokens and written identifiers;
* which scripts are present;
* where closed-class deictics occur -- a routing hint, stamped
  ``non_authoritative``.

It deliberately carries **no** propositions, clause kinds, instruction/statement
labels, referents, or antecedent candidates. An earlier version carried all of
them, produced by hand-written lexicons of verb forms and conjunctions, and the
result was a small rule-based parser masquerading as deterministic
infrastructure: each new example needed another lexicon entry, and each entry
was a new way to be confidently wrong.

Those questions belong to the model-equipped semantic node, whose answer is then
checked against this envelope by ``textsemantics.validation``. The division:

    model              -> what does the text say?
    deterministic code -> is that answer admissible, and what may we store?
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from memcore.textsemantics.blocks import TextBlock, scan_blocks
from memcore.textsemantics.contract import (
    DEFAULT_NORMALIZATION_CONTRACT,
    NormalizationContract,
)
from memcore.textsemantics.normalize import NormalizedText, normalize_with_mapping
from memcore.textsemantics.referential import (
    REFERENTIAL_CONTRACT_VERSION,
    ContextDependencyHint,
    detect_context_dependency,
    requires_conversation_context,
)
from memcore.textsemantics.segmentation import (
    SEGMENTATION_CONTRACT_VERSION,
    SentenceSpan,
    language_hints,
    segment_sentences,
)
from memcore.textsemantics.tokens import Token, identifier_phrases, tokenize
from memcore.textsemantics.validation import VALIDATION_CONTRACT_VERSION

TEXT_SEMANTICS_CONTRACT_VERSION = "memorist.text.envelope.v3"


class PhraseKind(StrEnum):
    """Why a run of tokens is reported as one phrase."""

    IDENTIFIER = "identifier"


@dataclass(frozen=True)
class PhraseSpan:
    """A written identifier recovered from the tokens it was split into."""

    kind: PhraseKind
    index: int
    text: str
    raw_text: str
    raw_start: int
    raw_end: int
    token_start: int
    token_end: int
    in_code: bool


@dataclass(frozen=True)
class TextEnvelope:
    """Everything deterministic code can say about one piece of raw text."""

    contract_version: str
    raw_text_hash: str
    language_hints: tuple[str, ...]
    blocks: tuple[TextBlock, ...]
    sentences: tuple[SentenceSpan, ...]
    normalized_text: str
    tokens: tuple[Token, ...]
    phrases: tuple[PhraseSpan, ...]
    context_dependency_hints: tuple[ContextDependencyHint, ...]
    warnings: tuple[str, ...]
    normalization_contract_version: str
    normalization_contract_fingerprint: str
    segmentation_contract_version: str = SEGMENTATION_CONTRACT_VERSION
    referential_contract_version: str = REFERENTIAL_CONTRACT_VERSION
    validation_contract_version: str = VALIDATION_CONTRACT_VERSION

    @property
    def requires_conversation_context(self) -> bool:
        """Whether the model should receive a wider context window.

        The only intended use of the deictic hints, and it is about how much
        history to send -- not about what the text means.
        """

        return requires_conversation_context(self.context_dependency_hints)

    def as_dict(self) -> dict[str, Any]:
        """JSON-serializable view.

        Raw text is not included. A consumer that needs the text already has
        it, and copying it into every audit payload would spread sensitive
        content across records that only need offsets and codes.
        """

        return {
            "contract_version": self.contract_version,
            "raw_text_hash": self.raw_text_hash,
            "language_hints": list(self.language_hints),
            "normalization_contract_version": self.normalization_contract_version,
            "normalization_contract_fingerprint": self.normalization_contract_fingerprint,
            "segmentation_contract_version": self.segmentation_contract_version,
            "referential_contract_version": self.referential_contract_version,
            "validation_contract_version": self.validation_contract_version,
            "requires_conversation_context": self.requires_conversation_context,
            "blocks": [
                {"kind": str(block.kind.value), "start": block.start, "end": block.end}
                for block in self.blocks
            ],
            "sentences": [
                {
                    "sentence_id": sentence.sentence_id,
                    "index": sentence.index,
                    "raw_start": sentence.raw_start,
                    "raw_end": sentence.raw_end,
                    "normalized_start": sentence.normalized_start,
                    "normalized_end": sentence.normalized_end,
                    "block_index": sentence.block_index,
                    "is_code": sentence.is_code,
                    "boundary_reason": sentence.boundary_reason,
                    "language_hints": list(sentence.language_hints),
                }
                for sentence in self.sentences
            ],
            "tokens": [
                {
                    "index": token.index,
                    "raw_start": token.raw_start,
                    "raw_end": token.raw_end,
                    "normalized_start": token.start,
                    "normalized_end": token.end,
                    "in_code": token.in_code,
                }
                for token in self.tokens
            ],
            "phrases": [
                {
                    "kind": str(phrase.kind.value),
                    "index": phrase.index,
                    "raw_start": phrase.raw_start,
                    "raw_end": phrase.raw_end,
                    "token_start": phrase.token_start,
                    "token_end": phrase.token_end,
                    "in_code": phrase.in_code,
                }
                for phrase in self.phrases
            ],
            "context_dependency_hints": [
                {
                    "raw_start": hint.raw_start,
                    "raw_end": hint.raw_end,
                    "authority": hint.authority,
                }
                for hint in self.context_dependency_hints
            ],
            "warnings": list(self.warnings),
        }

    def as_json(self) -> str:
        return json.dumps(self.as_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def raw_text_hash(raw: str) -> str:
    """SHA-256 over canonical UTF-8 bytes of the raw text."""

    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def build_envelope(
    raw: str,
    contract: NormalizationContract = DEFAULT_NORMALIZATION_CONTRACT,
) -> TextEnvelope:
    """Build the deterministic envelope for ``raw``.

    Pure and offline by construction: no I/O, no configuration lookup, no model
    call. Lite and Full both reach this function, which is what keeps the two
    runtimes from disagreeing about offsets, tokens, or code ranges.
    """

    normalized = normalize_with_mapping(raw, contract)
    sentences, warnings = segment_sentences(raw, normalized, contract)
    return TextEnvelope(
        contract_version=TEXT_SEMANTICS_CONTRACT_VERSION,
        raw_text_hash=raw_text_hash(raw),
        language_hints=language_hints(raw),
        blocks=scan_blocks(raw),
        sentences=sentences,
        normalized_text=normalized.text,
        tokens=tokenize(normalized, include_code=True),
        phrases=_phrases(normalized),
        context_dependency_hints=detect_context_dependency(raw, normalized, contract),
        warnings=_dedupe((*warnings, *_block_warnings(raw))),
        normalization_contract_version=normalized.contract_version,
        normalization_contract_fingerprint=normalized.contract_fingerprint,
    )


def _phrases(normalized: NormalizedText) -> tuple[PhraseSpan, ...]:
    phrases: list[PhraseSpan] = []
    for index, match in enumerate(identifier_phrases(normalized)):
        span = normalized.normalized_span(match.raw_start, match.raw_end)
        phrases.append(
            PhraseSpan(
                kind=PhraseKind.IDENTIFIER,
                index=index,
                text=match.text,
                raw_text=match.evidence,
                raw_start=match.raw_start,
                raw_end=match.raw_end,
                token_start=match.token_start,
                token_end=match.token_end,
                in_code=normalized.is_code(span[0]) if span is not None else False,
            )
        )
    return tuple(phrases)


def _block_warnings(raw: str) -> tuple[str, ...]:
    if raw.count("```") % 2 == 1:
        return ("unclosed_code_fence",)
    return ()


def _dedupe(values: tuple[str, ...]) -> tuple[str, ...]:
    seen: dict[str, None] = {}
    for value in values:
        seen.setdefault(value, None)
    return tuple(seen)
