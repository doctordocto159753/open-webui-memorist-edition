# Shared text semantics

`memcore.textsemantics` is the shared deterministic text **envelope** used by
Lite and Full. It owns transcription and evidence integrity, not semantic
interpretation.

```text
model              -> what does the text say?
deterministic code -> what characters exist, and is the model's evidence valid?
```

The package is pure: no persistence, I/O, configuration lookup, or model call.

## Why it exists

Older call sites used unrelated substring and alternation regexes. That caused
false matches such as:

- `تیم` inside `گرفتیم`;
- `تو` inside `توسعه`;
- `شما` inside `شمارش`;
- `token` inside `tokenizer`;
- `sk-` inside `task-force`.

The shared tokenizer and `Lexicon` replace arbitrary substring matching with
stable token/phrase boundaries while preserving exact raw evidence.

## Deterministic responsibilities

The package may establish:

- immutable raw text and a UTF-8 hash;
- normalized comparison text;
- raw ↔ normalized span mapping;
- token and identifier boundaries;
- fenced-code ranges and byte preservation;
- sentence spans on explicit punctuation or typed structure;
- closed-class deictic hints stamped `non_authoritative`;
- whether model evidence points to exact raw slices;
- whether reference ids and candidate lists are internally valid.

It may not establish:

- proposition or clause meaning;
- statement/instruction/question classification;
- transient versus durable intent;
- canonical polarity or its scope;
- epistemic status;
- referent candidates or selected referent;
- whether a proposition should become memory.

Those belong to the model-equipped semantic node and later deterministic policy.

## Normalization contract

`normalize_with_mapping(raw)` applies a versioned comparison transform:

| Rule | Behaviour |
| --- | --- |
| Unicode | NFC per base-plus-combining-mark cluster |
| Persian letters | `ي/ى → ی`, `ك → ک` |
| Arabic diacritics | removed only inside Arabic blocks |
| ZWNJ | controlled boundary policy |
| Digits | Persian/Arabic-Indic digits mapped to ASCII |
| Case | Unicode lowercase in the comparison view |
| Whitespace | compressed outside fenced code |
| Fenced code | copied byte for byte |

Every normalized character retains the raw range that produced it. Matching may
use normalized text, but persisted evidence must use the exact raw slice.

Contract: `memorist.text.normalization.v1`.

## Token boundaries and identifiers

Tokens are maximal alphanumeric runs plus combining marks not folded by NFC.
Punctuation, underscores, hyphens, dots, and ZWNJ are explicit boundaries.

`identifier_phrases` reconstructs written identifiers such as:

```text
GPT-5.4
MEMORIST_MEMORY_EXTRACTION_API_KEY
api-key
```

This preserves identifiers without weakening the token rules that keep `token`
out of `tokenizer`.

Fenced code is skipped by prose matching by default. Security-sensitive callers
may opt into code scanning while evidence remains byte-exact.

## Polarity: representation versus inference

Polarity remains a first-class representation:

```text
affirmed | negated | unknown
```

`memory_candidates.polarity` and `memory_versions.polarity` exist in both Lite
and Full. Existing rows default to `unknown`; no historical confidence is
recalculated. The old negation confidence penalty is removed because polarity
must not change extraction confidence.

The lexical functions `extract_polarity` and `is_hypothetical` are now
**non-authoritative diagnostic hints**. They may support tests or bounded repair,
but they do not create canonical candidate or memory semantics.

New deterministic modality payloads are stamped:

```json
{"semantic_authority": "non_authoritative"}
```

`read_modality_polarity` therefore returns `unknown` for them. New canonical
polarity may come only from a validated model semantic unit stamped
`validated_model`. Historical rows without an authority stamp remain readable
for compatibility and audit.

## Sentence spans: typography, not syntax

`segment_sentences` splits only on marks or structures the writer explicitly
provided:

- sentence terminators;
- blank lines;
- list markers;
- fenced-code blocks;
- block boundaries.

A bare newline is not syntax. A soft-wrapped sentence remains one span and emits
a warning. The package has no verb, conjunction, imperative, or clause-kind
lexicon.

Contract: `memorist.text.segmentation.v2`.

## Context-dependency hints

`detect_context_dependency` reports closed-class pronouns and demonstratives at
exact offsets. Each record is stamped `non_authoritative`.

The hint does not identify a referent. It can only expand a context window for
the model. The baseline is always non-zero:

```python
semantic_history_window_size(())             # 2 units
semantic_history_window_size(deictic_hints)  # 6 units
```

This matters because ellipsis and implicit continuation may need history without
containing an explicit pronoun.

Contract: `memorist.text.referential.v2`.

## `TextEnvelope`

```python
from memcore.textsemantics import build_envelope

envelope = build_envelope(raw)
envelope.blocks
envelope.sentences
envelope.tokens
envelope.phrases
envelope.context_dependency_hints
envelope.requires_conversation_context
envelope.as_json()
```

The envelope deliberately carries no propositions, clause kinds, instructions,
referents, antecedent candidates, or polarity cues. Its audit serialization
contains hashes, offsets, contract versions, and reason codes—not raw message
text.

Contract: `memorist.text.envelope.v3`.

## Evidence-integrity validation

WP01's validator does not validate semantic enum meaning. WP02 must first bind
model output to one strict closed typed schema.

Primary API:

```python
from memcore.textsemantics import validate_semantic_evidence

report = validate_semantic_evidence(raw, typed_model_output)
```

`validate_semantic_analysis` remains a compatibility alias.

Evidence validation checks:

- spans are in range and non-inverted;
- quoted evidence is byte-identical to the raw slice;
- units do not overlap or reuse ids;
- resolved references have a target;
- resolved references have a non-empty candidate list;
- every candidate id names an accepted unit;
- the selected target appears in that candidate list.

Nothing is repaired by guessing. If nothing trustworthy survives, the fallback
is `abstain` or `retain_raw_only`; neither creates memory.

Contract: `memorist.text.semantic_evidence_validation.v1`.

## Canonical and graph boundary

Text semantics never writes FalkorDB. Candidate and memory lineage is committed
to the canonical store first. Full mode may project it later through the
projection/outbox boundary. FalkorDB is a retrieval projection; PostgreSQL is
the final truth validator.

## API surface

```python
from memcore.textsemantics import (
    TextEnvelope,
    build_envelope,
    normalize_text,
    normalize_with_mapping,
    normalized_span_for_raw_span,
    raw_span_for_normalized_span,
    tokenize,
    contains_token,
    contains_phrase,
    identifier_phrases,
    detect_context_dependency,
    semantic_history_window_size,
    validate_semantic_evidence,
    ValidationReport,
    SemanticFallback,
    Polarity,
    Lexicon,
    scan_blocks,
)
```

See [Semantic analysis contract](semantic-analysis-contract.md) for the model and
WP02 boundary.
