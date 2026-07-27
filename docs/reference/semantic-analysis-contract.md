# Semantic analysis contract

Who decides what a message means, and who decides what may be stored from it.

```
model              -> what does the text say?
deterministic code -> is that answer admissible, and what may we keep?
```

This document exists because the boundary was crossed once. An earlier revision
of `memcore.textsemantics` tried to answer the first question in Python: it kept
a list of Persian and English verb forms to guess where a clause ended, a list of
conjunctions to guess whether `و` joined clauses or nouns, a list of imperatives
to label a clause an instruction, and a rule that offered nearby spans as
antecedents for a pronoun.

It worked on the examples it was written against. Every new example needed
another lexicon entry, and every entry created a new way to be confidently
wrong. That is what responsibility in the wrong layer looks like: the effort
grows without bound and the confidence is unearned. Deterministic code cannot
weigh what a conversation has been about, and it should not pretend to.

## Authority chain

```
raw message (immutable)
  -> deterministic technical envelope        memcore.textsemantics.build_envelope
  -> semantic analysis node (model)          full message + bounded history window
  -> strict structured output
  -> deterministic validator                 memcore.textsemantics.validation
  -> one bounded repair attempt
  -> conservative abstention on failure
  -> gate                                    server-authoritative policy
  -> candidate completeness planner          WP02
  -> canonical persistence
```

**The model never writes a memory.** It produces a semantic *proposal*.
Deterministic code owns authority, scope, privacy, gating, and persistence. A
proposal that fails validation is dropped or abstained on; it is never repaired
by guessing what the model meant.

## What deterministic code owns

| Responsibility | Where |
| --- | --- |
| Raw text immutability, UTF-8 integrity, content hash | `normalize.py`, `result.py` |
| Raw ↔ normalized offset mapping | `normalize.py` |
| Token boundaries, written identifiers | `tokens.py` |
| Fenced-code detection and byte preservation | `blocks.py` |
| Spans delimited by explicit punctuation and structure | `segmentation.py` |
| Closed-class deictic hints (`non_authoritative`) | `referential.py` |
| Validating the model's answer against raw text | `validation.py` |
| Fail-closed fallback | `validation.SemanticFallback` |
| Gate, privacy, scope, persistence | `memory_worker/semantic/*` |

Everything above is decidable from characters and offsets. None of it requires
an opinion about meaning.

## What the model owns

- proposition / clause segmentation
- proposition kind: statement, instruction, question, explanation
- transient versus durable intent
- polarity and its scope
- epistemic status (asserted, hedged, hypothetical)
- referential markers and their candidates
- the selected referent
- which propositions are candidate-worthy
- relations between propositions

The node must see the **whole message**, not one sentence at a time, and for
context-dependent references a **bounded window of previous messages**.
`AnalysisRequest` already carries `previous_units` and `next_units`, so the
transport for this exists.

`TextEnvelope.requires_conversation_context` is the deterministic signal for how
much history to send. It is derived from closed-class deictics only — pronouns
and demonstratives are a closed class, so unlike verb forms the list does not
grow with every new example. It says "this message probably needs context", and
nothing about what the context is.

## Structured output

```json
{
  "semantic_units": [
    {
      "id": "u1",
      "raw_start": 74,
      "raw_end": 110,
      "evidence": "حذف لایه WSL2.",
      "proposition": "Kubuntu's advantage is removing the WSL2 layer.",
      "unit_type": "preference",
      "polarity": "affirmed",
      "epistemic_status": "asserted",
      "durability": "durable"
    }
  ],
  "references": [
    {
      "marker_start": 145,
      "marker_end": 153,
      "marker_evidence": "این مزیت",
      "status": "resolved",
      "target_unit_id": "u1",
      "candidate_unit_ids": ["u1", "u2"],
      "confidence": "high"
    }
  ]
}
```

`proposition` is a paraphrase and may be in any language. `evidence` is **not**
a paraphrase: it must be the exact slice `raw[raw_start:raw_end]`.

## What the validator rejects

`validate_semantic_analysis(raw, payload)` returns a `ValidationReport` naming
the admissible subset and every rejection. It never raises on bad model output
and never repairs it.

| Violation | Meaning |
| --- | --- |
| `span_out_of_range` | span falls outside the message |
| `span_inverted` | `raw_start >= raw_end` |
| `evidence_not_a_slice` | quoted evidence is not byte-identical to the span |
| `evidence_missing` | no evidence quoted |
| `units_overlap` | two units claim the same characters |
| `duplicate_unit_id` | same id twice |
| `unknown_unit_id` | a reference targets a unit that does not exist |
| `referent_not_a_candidate` | the model chose outside its own candidate list |
| `resolved_without_target` | `status: resolved` with no target |
| `malformed_record` | missing or wrongly typed fields |

`evidence_not_a_slice` is the important one. A model that tidies whitespace,
drops a ZWNJ, or fixes a typo has produced text that no longer addresses the
stored message. Accepting it would put unauditable text into evidence, so a
one-character difference is a rejection.

`referent_not_a_candidate` is the anti-hallucination check for resolution: a
model may only select from options it itself put forward.

## Fail-closed fallback

When no trustworthy analysis exists, deterministic code does **not** reconstruct
meaning from rules. It declines:

| Outcome | When |
| --- | --- |
| `abstain` | no analysis at all — model unavailable, disabled, or returned nothing |
| `retain_raw_only` | an analysis arrived and nothing in it survived validation |
| `needs_review` | admissible but policy wants a human |

None of these creates a memory. Losing a memory is recoverable — the raw message
is still there and can be reprocessed under a later contract version. Storing a
wrong one is not.

This is why the deterministic Jakobson fallback must never be extended into a
substitute semantic analyser. Its job is to keep the pipeline moving without a
model, not to produce claims.

## For WP02

Consume the **validated** model analysis, not the envelope's raw fields:

```python
envelope = build_envelope(raw)                      # offsets and integrity
report = validate_semantic_analysis(raw, analysis)  # admissibility
if report.fallback is not None:
    ...                                             # abstain / retain raw
units = [u for u in analysis["semantic_units"] if u["id"] in report.accepted_unit_ids]
```

The envelope is what the model's citations are *checked against*. It is not a
source of propositions, and it deliberately exposes none — no clauses, no clause
kinds, no referents, no antecedent candidates. `TextEnvelope` asserts their
absence in test.

Contract versions: `memorist.text.envelope.v3`,
`memorist.text.semantic_validation.v1`, `memorist.text.segmentation.v2`,
`memorist.text.referential.v2`.

## Related

- [Shared text semantics](text-semantics.md) — the deterministic envelope
- [Memory worker prompts](memory-worker-prompts.md) — prompt bundle contract
- [Runtime role contracts](runtime-role-contracts.md) — processing-node roles
