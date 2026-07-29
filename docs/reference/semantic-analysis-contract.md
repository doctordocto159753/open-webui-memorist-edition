# Semantic analysis contract

Who decides what a message means, and who decides what may be stored from it.

```text
model              -> what does the text say?
deterministic code -> is that answer admissible, and what may we keep?
```

This boundary is explicit because an earlier WP01 revision started building a
rule-based parser: verb lists guessed clause endings, conjunction lists guessed
whether `و` joined clauses or nouns, and nearby spans were offered as pronoun
antecedents. Each failing example produced another rule and another confident
failure. Semantic authority was in the wrong layer.

## Authority chain

```text
raw message (immutable)
  -> deterministic technical envelope
  -> semantic analysis node (model; whole message + bounded prior context)
  -> strict typed output contract                    WP02
  -> evidence-integrity validator                    WP01
  -> one bounded repair attempt
  -> conservative abstention on failure
  -> gate / privacy / scope                          deterministic policy
  -> candidate completeness planner                  WP02
  -> canonical PostgreSQL or SQLite persistence
  -> projection/outbox                               Full may project to FalkorDB
```

The model never writes a memory. It produces a semantic proposal. Deterministic
code owns identity, authority, privacy, gating, scope, evidence integrity,
idempotency, and persistence.

## What deterministic code owns

| Responsibility | Location |
| --- | --- |
| Raw immutability, UTF-8 integrity, content hash | `textsemantics.normalize`, `result` |
| Raw ↔ normalized offset mapping | `textsemantics.normalize` |
| Token boundaries and written identifiers | `textsemantics.tokens` |
| Fenced-code byte preservation | `textsemantics.blocks` |
| Spans delimited by explicit punctuation/structure | `textsemantics.segmentation` |
| Closed-class deictic hints stamped `non_authoritative` | `textsemantics.referential` |
| Evidence and reference-link integrity | `textsemantics.validation` |
| Fail-closed fallback | `SemanticFallback` |
| Gate, privacy, scope, persistence | `memory_worker.semantic/*` |

Lexical polarity and hypothetical detectors may remain as diagnostics or bounded
repair hints. They are not canonical semantic decisions.

## What the model owns

- proposition/clause segmentation;
- statement, instruction, question, explanation;
- transient versus durable intent;
- polarity and its scope;
- epistemic status: asserted, hedged, hypothetical;
- referential markers, candidate referents, and selected referent;
- candidate-worthy propositions;
- relations between propositions.

The semantic node sees the whole message and a bounded prior-context window.
History has a non-zero baseline even when no deictic is detected. A closed-class
hint may expand the window, but never reduce the baseline to zero; ellipsis and
implicit continuation do not always contain a pronoun.

Current policy helpers:

```python
semantic_history_window_size(())              # baseline, currently 2 units
semantic_history_window_size(deictic_hints)   # expanded, currently 6 units
```

## Polarity authority

Representation and inference are separate:

- `Polarity` enum and candidate/version columns are deterministic storage
  contracts;
- removal of the negation confidence penalty remains valid;
- historical `negated` payloads remain readable for audit compatibility;
- new lexical hints are stamped `semantic_authority=non_authoritative`;
- candidate creation reads those hints as `unknown`;
- only a validated model semantic unit stamped `validated_model` may supply new
  canonical polarity and epistemic status.

Without validated model analysis:

```text
polarity = unknown
epistemic_status = unknown
fallback = abstain | retain_raw_only | needs_review
```

No absence-of-negation rule may silently mean `affirmed` in canonical memory.

## Strict typed output: semantic candidate analysis v1

WP02 implements the closed `memorist.semantic_candidate_analysis` contract,
version `1.0`, schema name `memorist_semantic_candidate_analysis_v1`. Pydantic
strict mode rejects extra, missing, mistyped, and out-of-enum fields before the
WP01 evidence validator runs.

At minimum a semantic unit declares:

```json
{
  "id": "u1",
  "raw_start": 74,
  "raw_end": 110,
  "evidence": "حذف لایه WSL2.",
  "proposition": "Kubuntu's advantage is removing the WSL2 layer.",
  "unit_type": "statement",
  "polarity": "affirmed",
  "epistemic_status": "asserted",
  "durability": "durable"
}
```

`unit_type` is exactly `statement | instruction | question | explanation`.
Durability is `durable | transient | context_only | unknown`; polarity and
epistemic status are separate axes. The runtime sequence is parse → strict
contract → semantic binding → WP01 evidence validation → at most one repair →
content-free `abstain` fallback. Fallback creates no proposal or memory.

## Evidence-integrity validation

Primary API:

```python
report = validate_semantic_evidence(raw, typed_model_output)
```

`validate_semantic_analysis` remains a compatibility alias. The explicit name
prevents a false claim: WP01 validates citations and links, not semantic meaning
or enum correctness.

The validator rejects:

| Violation | Meaning |
| --- | --- |
| `span_out_of_range` | span is outside the message |
| `span_inverted` | `raw_start >= raw_end` |
| `evidence_not_a_slice` | evidence is not byte-identical to the raw slice |
| `evidence_missing` | no evidence was supplied |
| `units_overlap` | two units claim overlapping characters |
| `duplicate_unit_id` | one id is reused |
| `unknown_unit_id` | selected target is not an accepted unit |
| `candidate_list_required` | a resolved reference has no non-empty candidate list |
| `unknown_candidate_unit_id` | a candidate id is not an accepted unit |
| `referent_not_a_candidate` | selected target is outside the offered candidates |
| `resolved_without_target` | resolved status has no target |
| `malformed_record` | evidence-level fields are malformed |

A resolved reference must satisfy all of these:

```text
candidate_unit_ids is non-empty
every candidate id names an accepted semantic unit
target_unit_id names an accepted unit
target_unit_id is inside candidate_unit_ids
```

A dropped ZWNJ, normalized whitespace, or corrected typo is an evidence failure.
The system does not guess how to repair it.

## Fail-closed fallback

| Outcome | When |
| --- | --- |
| `abstain` | no model analysis exists |
| `retain_raw_only` | analysis arrived but no unit survived evidence validation |
| `needs_review` | admissible output requires human policy review |

None creates a memory by itself. Raw input remains available for replay under a
later contract version.

## Projection boundary

Candidate creation and consolidation preserve the complete canonical lineage
required by later retrieval:

```text
Memory -> MemoryVersion -> Candidate -> Evidence -> Route
```

WP01/WP02 never write FalkorDB directly. In Full mode, projection occurs only
after canonical persistence through the existing projection/outbox boundary.
FalkorDB may later propose retrieval paths; PostgreSQL remains the final truth
validator.

## For WP02

WP02 consumes:

```text
TextEnvelope
+ strict typed model SemanticAnalysis
+ evidence-admissible unit/reference ids
+ server-authoritative gate/route/provenance
```

It must not consume lexical polarity hints or deictic hints as memory facts.

Contract versions at this boundary:

- `memorist.text.envelope.v3`
- `memorist.text.semantic_evidence_validation.v1`
- `memorist.text.segmentation.v2`
- `memorist.text.referential.v2`

## Related

- [Shared text semantics](text-semantics.md)
- [Memory worker prompts](memory-worker-prompts.md)
- [Runtime role contracts](runtime-role-contracts.md)
