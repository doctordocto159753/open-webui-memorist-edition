# Memory Intelligence Core

The Memory Intelligence Core turns immutable message evidence into persisted
communication annotations, route/gate authority, complete semantic coverage,
and replay-safe memory candidates. Lite and Full use the same semantic
orchestration; their storage adapters differ.

## Current data flow

```text
messages.raw_text
  -> TextEnvelope v3
  -> text_units(exact offsets and hashes)
  -> memorist.jakobson_sentence_analysis v3.0
  -> prompt/stage/attempt audit
  -> jakobson_sentence_annotations
  -> memory_signal_routes
  -> gate_decisions
  -> bounded same-authority context (2 units, or 6 for dependency hints)
  -> memorist.semantic_candidate_analysis v1.0
  -> strict schema + semantic binding + WP01 exact-evidence validation
  -> deterministic CoveragePlan
  -> deterministic CandidateProposal UUIDv5
  -> memory_candidates + candidate_evidence
  -> consolidation -> memories + memory_versions
  -> rebuildable FTS/embedding/graph projections
```

The `memory-extraction-contract-bundle-v1` certification bundle contains
Jakobson v3 and semantic candidate v1 in that order. One configured profile
must pass both contracts. Prompt Pack remains version `2.0`; adding the
semantic contract does not rewrite historical prompt versions.

## Authority boundaries

The model may return communication factors, semantic propositions,
references, relations, durability, polarity, and epistemic status. It does not
own:

- exact evidence acceptance;
- canonical route or gate;
- privacy or provenance ceilings;
- coverage disposition;
- proposal/candidate identity;
- persistence, consolidation, or graph authority.

`discard` and `retain_raw_only` gates stop before semantic model execution and
candidate creation. `manual_review` may receive audit coverage but cannot
create an automatic candidate. Candidate adapters re-read persisted authority
immediately before transactional persistence.

## Jakobson annotation

Each text unit records the six communication factors:

| Factor | Purpose |
| --- | --- |
| `sender_addresser` | apparent speaker or actor |
| `receiver_addressee` | addressed receiver |
| `message` | communicated content |
| `context_referent` | referenced topic, process, object, or event |
| `code` | language, register, genre, and shared terminology |
| `contact_channel` | communication channel or relation |

Jakobson is an annotation lens, not memory. Legacy aggregate linguistic
analysis is auxiliary and cannot override route/gate or semantic coverage.

## Complete semantic coverage

`SemanticCandidatePlanningService` invokes semantic v1 once for the whole
eligible message. It resolves prior context only from the same trusted user,
session, workspace, and project. System/tool records, current/future turns,
deleted/hidden/redacted versions, sensitive text, stale spans, attachments,
and cross-boundary records are excluded.

Every accepted unit receives exactly one closed disposition:

`durable_candidate`, `context_only`, `transient_instruction`,
`unresolved_reference`, `rejected_by_gate`, `needs_review`, or `unsupported`.

Material omitted by the model is represented explicitly as
`unsupported/uncovered_material`. An ambiguous reference remains unresolved.
Only `durable_candidate` creates one proposal.

## Evidence and replay

Exact raw spans link proposals to text units and messages. Proposal identity is
UUIDv5 over canonical contract, message, semantic closure, route/gate,
authority, span, and disposition material; timestamps, execution IDs, provider
metadata, warnings, confidence hints, and model IDs are excluded.

Coverage plan, reservation, candidate, evidence, and link persistence is
idempotent. A restart with the same identity returns the existing linked
candidate; a different payload hash is an identity conflict.

## Projections

SQLite or PostgreSQL is canonical. FTS, embeddings, active blocks, attachments,
and FalkorDB are rebuildable projections or delivery artifacts. They may help
retrieval but cannot create or upgrade semantic authority.

For field-level contracts, read
[Semantic candidate authority](semantic-candidate-authority.md). For an
end-to-end example, read
[Walkthrough پردازش حافظه در موتور مرکزی](core-memory-processing-walkthrough.md).
