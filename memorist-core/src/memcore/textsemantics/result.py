"""The one typed view of a piece of text that downstream packages consume.

Everything here is derived from immutable raw text and nothing here is
authoritative on its own. Two guarantees make it safe to build on:

* every span-bearing record carries the exact raw ``[start, end)`` that produced
  it, so a consumer can persist auditable evidence without ever storing
  normalized text as evidence;
* the same raw text under the same ``contract_version`` produces a
  byte-equivalent result, because every function reached from here is pure.

The result is a snapshot of what deterministic rules can see, including what
they could not decide -- ``warnings`` and unresolved referential markers are
part of the contract, not omissions from it.
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
from memcore.textsemantics.polarity import (
    POLARITY_CONTRACT_VERSION,
    Polarity,
    extract_polarity,
)
from memcore.textsemantics.referential import (
    REFERENTIAL_CONTRACT_VERSION,
    ReferentialMarker,
    detect_referential_markers,
)
from memcore.textsemantics.segmentation import (
    SEGMENTATION_CONTRACT_VERSION,
    ClauseSpan,
    SentenceSpan,
    language_hints,
    segment_clauses,
    segment_sentences,
)
from memcore.textsemantics.tokens import Token, identifier_phrases, tokenize

TEXT_SEMANTICS_CONTRACT_VERSION = "memorist.text.semantics.v2"


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
class PolarityCue:
    """Clause-level polarity with the raw evidence that decided it."""

    clause_id: str
    clause_index: int
    polarity: Polarity
    marker: str | None
    evidence: str | None
    raw_start: int | None
    raw_end: int | None


@dataclass(frozen=True)
class TextSemanticsResult:
    """One deterministic semantic view of one piece of raw text."""

    contract_version: str
    raw_text_hash: str
    language_hints: tuple[str, ...]
    blocks: tuple[TextBlock, ...]
    sentences: tuple[SentenceSpan, ...]
    clauses: tuple[ClauseSpan, ...]
    normalized_text: str
    tokens: tuple[Token, ...]
    phrases: tuple[PhraseSpan, ...]
    polarity_cues: tuple[PolarityCue, ...]
    referential_markers: tuple[ReferentialMarker, ...]
    warnings: tuple[str, ...]
    normalization_contract_version: str
    normalization_contract_fingerprint: str
    segmentation_contract_version: str = SEGMENTATION_CONTRACT_VERSION
    polarity_contract_version: str = POLARITY_CONTRACT_VERSION
    referential_contract_version: str = REFERENTIAL_CONTRACT_VERSION

    def clause(self, clause_id: str) -> ClauseSpan | None:
        return next((item for item in self.clauses if item.clause_id == clause_id), None)

    def markers_requiring_context(self) -> tuple[ReferentialMarker, ...]:
        return tuple(item for item in self.referential_markers if item.requires_context)

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
            "polarity_contract_version": self.polarity_contract_version,
            "referential_contract_version": self.referential_contract_version,
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
                    "language_hints": list(sentence.language_hints),
                }
                for sentence in self.sentences
            ],
            "clauses": [
                {
                    "clause_id": clause.clause_id,
                    "index": clause.index,
                    "sentence_index": clause.sentence_index,
                    "block_index": clause.block_index,
                    "kind": str(clause.kind.value),
                    "raw_start": clause.raw_start,
                    "raw_end": clause.raw_end,
                    "normalized_start": clause.normalized_start,
                    "normalized_end": clause.normalized_end,
                    "boundary_reason": clause.boundary_reason,
                    "is_code": clause.is_code,
                }
                for clause in self.clauses
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
            "polarity_cues": [
                {
                    "clause_id": cue.clause_id,
                    "clause_index": cue.clause_index,
                    "polarity": str(cue.polarity.value),
                    "raw_start": cue.raw_start,
                    "raw_end": cue.raw_end,
                }
                for cue in self.polarity_cues
            ],
            "referential_markers": [
                {
                    "marker_type": str(marker.marker_type.value),
                    "raw_start": marker.raw_start,
                    "raw_end": marker.raw_end,
                    "normalized_start": marker.normalized_start,
                    "normalized_end": marker.normalized_end,
                    "clause_id": marker.clause_id,
                    "clause_index": marker.clause_index,
                    "requires_context": marker.requires_context,
                    "resolution_scope_hint": str(marker.resolution_scope_hint.value),
                    "confidence_label": str(marker.confidence_label.value),
                    "reason_codes": list(marker.reason_codes),
                    "antecedent_candidate_spans": [
                        {
                            "clause_id": candidate.clause_id,
                            "clause_index": candidate.clause_index,
                            "raw_start": candidate.raw_start,
                            "raw_end": candidate.raw_end,
                            "distance_clauses": candidate.distance_clauses,
                            "reason_codes": list(candidate.reason_codes),
                        }
                        for candidate in marker.antecedent_candidate_spans
                    ],
                }
                for marker in self.referential_markers
            ],
            "warnings": list(self.warnings),
        }

    def as_json(self) -> str:
        return json.dumps(self.as_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def raw_text_hash(raw: str) -> str:
    """SHA-256 over canonical UTF-8 bytes of the raw text."""

    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def analyze_text(
    raw: str,
    contract: NormalizationContract = DEFAULT_NORMALIZATION_CONTRACT,
) -> TextSemanticsResult:
    """Build the full semantic view of ``raw``.

    Pure and offline by construction: no I/O, no configuration lookup, no
    model call. Lite and Full both reach this function, which is what keeps the
    two runtimes from drifting apart on what a sentence, a clause, or a
    negation is.
    """

    normalized = normalize_with_mapping(raw, contract)
    sentences, sentence_warnings = segment_sentences(raw, normalized, contract)
    clauses, clause_warnings = segment_clauses(raw, sentences, normalized, contract)
    markers, marker_warnings = detect_referential_markers(raw, clauses, normalized, contract)
    warnings = _dedupe(
        (*sentence_warnings, *clause_warnings, *marker_warnings, *_block_warnings(raw))
    )
    return TextSemanticsResult(
        contract_version=TEXT_SEMANTICS_CONTRACT_VERSION,
        raw_text_hash=raw_text_hash(raw),
        language_hints=language_hints(raw),
        blocks=scan_blocks(raw),
        sentences=sentences,
        clauses=clauses,
        normalized_text=normalized.text,
        tokens=tokenize(normalized, include_code=True),
        phrases=_phrases(normalized),
        polarity_cues=_polarity_cues(normalized, clauses),
        referential_markers=markers,
        warnings=warnings,
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


def _polarity_cues(
    normalized: NormalizedText,
    clauses: tuple[ClauseSpan, ...],
) -> tuple[PolarityCue, ...]:
    """Polarity per clause, so a negated clause cannot flip its neighbour.

    Running the detector over a whole message would let one negation mark every
    claim in it. Clause scope is the smallest unit this contract can defend.
    """

    cues: list[PolarityCue] = []
    for clause in clauses:
        if clause.is_code or clause.normalized_start is None or clause.normalized_end is None:
            cues.append(
                PolarityCue(
                    clause_id=clause.clause_id,
                    clause_index=clause.index,
                    polarity=Polarity.UNKNOWN,
                    marker=None,
                    evidence=None,
                    raw_start=None,
                    raw_end=None,
                )
            )
            continue
        slice_text = normalized.text[clause.normalized_start : clause.normalized_end]
        result = extract_polarity(slice_text)
        # ``extract_polarity`` ran against a detached slice, so the offsets it
        # returns index that slice, not the message. Rather than shifting them
        # -- which would silently assume normalizing normalized text never
        # moves a character -- the marker is located again in the real token
        # stream, so the persisted evidence span is exact by construction.
        raw_start, raw_end = _marker_span(normalized, clause, result.marker)
        cues.append(
            PolarityCue(
                clause_id=clause.clause_id,
                clause_index=clause.index,
                polarity=result.polarity,
                marker=result.marker,
                evidence=(
                    normalized.raw[raw_start:raw_end]
                    if raw_start is not None and raw_end is not None
                    else None
                ),
                raw_start=raw_start,
                raw_end=raw_end,
            )
        )
    return tuple(cues)


def _marker_span(
    normalized: NormalizedText,
    clause: ClauseSpan,
    marker: str | None,
) -> tuple[int | None, int | None]:
    """Locate a polarity marker inside one clause, in real raw coordinates.

    Returns ``(None, None)`` when there is no marker, or when the marker is a
    bound prefix that no whole token equals -- the polarity still stands, only
    its span is unavailable, and reporting no span is honest where reporting an
    approximate one would not be.
    """

    if not marker:
        return None, None
    tokens = [
        token
        for token in tokenize(normalized)
        if token.raw_start >= clause.raw_start and token.raw_end <= clause.raw_end
    ]
    needle = tuple(item.key for item in tokenize(marker))
    if not needle:
        return None, None
    for offset in range(len(tokens) - len(needle) + 1):
        window = tokens[offset : offset + len(needle)]
        if tuple(token.key for token in window) == needle:
            return window[0].raw_start, window[-1].raw_end
    if len(needle) == 1:
        for token in tokens:
            if token.key.startswith(needle[0]):
                return token.raw_start, token.raw_end
    return None, None


def _block_warnings(raw: str) -> tuple[str, ...]:
    if raw.count("```") % 2 == 1:
        return ("unclosed_code_fence",)
    return ()


def _dedupe(values: tuple[str, ...]) -> tuple[str, ...]:
    seen: dict[str, None] = {}
    for value in values:
        seen.setdefault(value, None)
    return tuple(seen)
