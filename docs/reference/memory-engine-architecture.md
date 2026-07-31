# Memorist Memory Engine Architecture

This is the current long-form architecture reference for the memory engine in
Open WebUI Memorist Edition. It describes implemented boundaries, not a future
roadmap.

Current baseline:

```text
development release: 0.2.0-beta.3
storage schema: 27
SQLite migration head: 0038_message_first_semantics.sql
PostgreSQL migration head: 0025_message_first_semantics.sql
Prompt Pack: 2.0
Jakobson: memorist.jakobson_sentence_analysis 3.0
Semantic analysis: memorist.semantic_candidate_analysis 1.1
Preflight planning: memorist.preflight_planning 2.1
role manifest: role-contract-manifest-v3
```

The root [README](../../README.md) covers product entry points. The compact
[Memory Machine](../MEMORY_MACHINE.md) covers the lifecycle. The
[central processing walkthrough](core-memory-processing-walkthrough.md)
follows one concrete prompt and response through the actual source paths.

## Architectural thesis

```text
A message is canonical retrievable evidence and a first-class graph node.
A model output is a proposal, not authority.
A candidate is a routed interpretation, not truth.
A memory is a versioned, evidence-linked claim.
A projection is rebuildable, not canonical.
Retrieved memory is data, not instruction.
```

These separations prevent one model response, stale graph edge, attachment, or
client-side control from silently becoming durable user truth.

## System boundary

```text
Open WebUI
  -> server-side Memorist Filter
       inlet: policy, user capture, preflight recall, separate attachment
       outlet: assistant capture and response/attachment linkage
  -> authenticated Memorist backend proxy

memorist-core
  -> canonical evidence ledger
  -> worker and semantic orchestration
  -> consolidation and memory versions
  -> retrieval and attachment builder
  -> Model Control Plane
  -> import, Heritage, forget, diagnostics

derived systems
  -> FTS
  -> embeddings
  -> active blocks
  -> FalkorDB graph (Full)
```

Open WebUI owns the visible chat and main model. Memorist observes main-model
metadata only, so it can size attachment budgets and attribute delivery.

## Two connected runtime flows

### Recall before the main model

```text
Filter.inlet
-> trusted actor and turn policy
-> session resolution
-> idempotent user-message capture
-> dynamic attachment budget
-> model-assisted intent/topic/entity/process/stage retrieval plan
-> canonical memory plus scoped Message-evidence candidate generation
-> deterministic score/rerank/select or abstain
-> bounded Memory Context Attachment
-> delivery record
-> separate memorist_context message
-> Open WebUI main model
```

The current user message is captured before retrieval, but it is not yet a
consolidated memory and therefore cannot be recalled retroactively into the
same preflight.

### Learning after capture

```text
captured user or assistant message
-> processing identity and authority snapshot
-> TextEnvelope, structural blocks and exact text units
-> bounded-context resolver
-> whole-message semantic candidate analysis v1
-> Message summary/categories/topics/concepts/entity/process-stage ledger
-> persisted Jakobson/route/gate compatibility annotations
-> strict and exact-evidence validation
-> deterministic coverage plan
-> deterministic proposal/candidate identity
-> transactional candidate/evidence/link persistence
-> candidate stages
-> consolidation
-> memory/version
-> projection outboxes
```

The Filter outlet records the assistant response only after the main model
completes. It links response, input, attachment, and provider response identity,
then queues extraction. Assistant content remains `assistant_claim` unless a
later current-user statement explicitly and uniquely ratifies or corrects it.

## Canonical data layers

### Evidence ledger

Messages, immutable message versions, session events, text units, and exact
spans preserve what happened. Downstream systems link to evidence; they do not
reconstruct quotes from normalized text.

### Analysis and authority ledger

Jakobson annotations, routes, gates, prompt executions, stage runs, and
provider-attempt reservations explain how evidence was interpreted. Remote
calls are reserved before network I/O and finalized afterward. A lost
completion is not silently retried as if no paid call occurred.

### Candidate and coverage ledger

Coverage runs/items make omission explicit. Proposal reservations and candidate
links make persistence replay-safe. Content-free audit tables do not duplicate
raw evidence.

### Memory ledger

`memories` holds canonical identity and scope; `memory_versions` holds changing
values and validity. Consolidation records create, reinforce, supersede,
contradict, noop, manual-review, or reject decisions instead of overwriting
history.

### Retrieval and delivery ledger

Retrieval runs, expanded queries, candidate scores, selection, attachments,
delivery events, and response links distinguish “eligible,” “selected,”
“delivered,” and “used for this response.”

## Semantic authority ordering

The order is fixed:

```text
capture
-> TextEnvelope v3
-> text units
-> Jakobson v3
-> persisted route
-> persisted gate
-> bounded-context resolver
-> semantic candidate analysis v1
-> strict schema validation
-> WP01 exact-evidence validation
-> deterministic coverage planner
-> existing candidate service
```

Legacy `discard`, `retain_raw_only`, and `manual_review` values remain audit
annotations; they do not veto the whole-message semantic contract. Candidate
adapters re-read persisted source, scope, privacy, and any available legacy
lineage immediately before persistence.

The model may propose semantic units, structural unit type, memory kind,
propositions, references, relations, durability, polarity, and epistemic
status. Deterministic code owns privacy, provenance, evidence acceptance,
coverage disposition, identity, and writes. Route/gate/Jakobson values are
optional compatibility metadata, not semantic authority.

## Complete coverage and deterministic identity

Each accepted semantic unit gets exactly one disposition:

- `durable_candidate`;
- `context_only`;
- `transient_instruction`;
- `unresolved_reference`;
- `rejected_by_gate`;
- `needs_review`;
- `unsupported`.

Material tokens outside accepted units are grouped into deterministic
`unsupported/uncovered_material` items. Ambiguous references remain
unresolved. Only a durable candidate creates exactly one proposal.

The proposal UUID is UUIDv5 over a canonical SHA-256 identity that includes
message/raw hash, contract and semantic closure, span, route/gate, source
authority, and disposition. Timestamps, random execution IDs, provider
metadata, warnings, confidence hints, and model-chosen IDs are excluded.

## Bounded context and authority ceilings

Semantic context comes from canonical prior message versions and text units,
not retrieval attachments. The baseline is two prior units and expands to six
only for dependency hints.

Every record must have:

- the same trusted user and session;
- the same workspace and project;
- a strictly earlier turn;
- visible, non-deleted, non-redacted content;
- an immutable version and exact valid span;
- role `user` or `assistant`;
- normal sensitivity classification.

System prompts, tool output, `memorist_context`, Memory Context Attachments,
current/future turns, stale spans, and cross-boundary records are excluded.
Assistant context is capped at `assistant_claim`.

## Consolidation

Candidates require evidence. The consolidator derives scope from the canonical
session and a canonical key from the mapped candidate. It:

- creates the first memory/version;
- reinforces an existing version with new evidence;
- supersedes by closing the prior version and creating a new one;
- records contradiction without flattening it;
- records manual review, noop, or rejection without creating a false memory.

Consolidation is deterministic and idempotent for a candidate UUID.

## Retrieval and attachment

The versioned Preflight `2.1` model proposes bounded query understanding; local
code enforces user/workspace/project scope and persists the plan for audit.
Hybrid generation can use canonical memory, exact Message Evidence, active
constraints, FTS, embeddings, and optional graph projection. Lite and Full use
the same model-led query fields and scope rules. Deterministic scoring records
authority, confidence, time, and conflict contributions; safe reranking cannot
bypass scope.

Selection may abstain. The attachment builder applies a dynamic token budget,
escapes delimiters, marks instruction-like content, records provenance, and
stores the rendered packet. The Filter transports it as a separate system-role
message named `memorist_context`, but that transport shape does not make it a
trusted instruction.

The “Memory used” UI is read-only and built from authorized delivery records,
not from browser claims or model text.

## Lite and Full

| Boundary | Lite | Full |
| --- | --- | --- |
| Canonical store | SQLite | PostgreSQL |
| Writes | serialized write actor / `BEGIN IMMEDIATE` where required | transactions, row locks, durable jobs |
| Semantic service | shared | shared |
| Coverage identity/policy | shared | shared |
| Persistence adapter | SQLite | PostgreSQL |
| Graph | not required | FalkorDB projection through outbox |
| Authority | SQLite ledger | PostgreSQL ledger |

The semantic decision must be identical for identical canonical inputs. Full
adds scale and projections, not a second semantic implementation.

## Model Control Plane

Roles are explicit:

- `main_chat_observed`;
- `preflight`;
- `memory_extraction`;
- `high_confidence_extraction`;
- `privacy_sensitivity`;
- `embedding`;
- `block_compaction`;
- `import_reconstruction`.

Resolution follows project, workspace, global, documented inheritance, then a
built-in fallback. Remote profiles require explicit privacy acknowledgement and
refer to secrets by environment-variable name only.

`memory_extraction` certification uses
`memory-extraction-contract-bundle-v1`: Jakobson v3 followed by semantic
candidate v1. One profile must pass both real runtime contracts. Endpoint,
model, capabilities, enabled state, secret reference, prompt text, or typed
contract changes invalidate certification.

## Import, Heritage, and forget

Imports are untrusted historical evidence. Detection, normalization, dry-run,
dedupe, commit, and optional reconstruction preserve source provenance.

Heritage exports canonical records as checksummed I-JSON/I-JSONL and supports
offline verification and restore. Derived indexes are rebuilt.

Forget is a dependency traversal: preview, confirm, quarantine, erase/redact,
invalidate projections, residue check, and content-free receipt. Semantic
coverage rows are audit metadata and are handled without copying erased raw
evidence into receipts.

## Idempotency and failure semantics

- Capture has stable idempotency keys.
- Assistant completion deduplicates provider response/content identities.
- Jobs are enqueued once and carry processing identity.
- Provider attempts are reserved before I/O.
- Route/gate/profile/source/contract authority is fenced around calls.
- Semantic execution has one repair budget and a valid abstention fallback.
- Coverage, proposal, candidate, evidence, and links are replay-safe.
- Consolidation returns the existing decision on replay.
- Outboxes make projection retries independent of canonical transactions.
- Preflight fails open for chat availability, never as a false memory success.

## Security properties and limits

Memory Off is server-enforced. Cross-session/user/workspace/project context is
fail-closed. Raw provider output and credentials are not stored in attempt
audit rows. Remote providers still receive role-specific payloads when the user
configures them. Local `.env` storage is plaintext and there is no encryption
at rest or completed independent security audit in this beta candidate.

Prompt injection is mitigated by data/instruction separation, escaping,
schema/evidence validation, authority ceilings, and deterministic policy; it
is not claimed to be eliminated.

## Validation architecture

The single `.github/workflows/ci-consolidated.yml` has four authoritative jobs:

1. Quality, Unit, Integration, and UI;
2. PostgreSQL, Full Runtime, and FalkorDB;
3. Package and Lifecycle;
4. One Deployment Product E2E.

Semantic acceptance includes independent golden cases, Persian and mixed text,
code fences, ambiguity, omission, authority mutation, parity, session
isolation, and replay/restart invariants.

## Related references

- [Central processing walkthrough](core-memory-processing-walkthrough.md)
- [Semantic candidate authority](semantic-candidate-authority.md)
- [Semantic analysis contract](semantic-analysis-contract.md)
- [Model Control Plane](model-control-plane.md)
- [Storage profiles](storage-profiles.md)
- [SQLite runtime](sqlite-runtime.md)
- [PostgreSQL](postgres.md)
- [FalkorDB](falkordb.md)
- [Preflight](preflight.md)
- [Import](import.md)
- [Heritage roundtrip](heritage-roundtrip.md)
- [Forget residue](forget-residue.md)

## Summary

Memorist is an evidence ledger plus a deterministic authority pipeline around
bounded model assistance. It stores what was said, records how it was
interpreted, makes omission explicit, persists candidates idempotently,
versions memory rather than rewriting history, and recalls through scoped,
auditable attachments. Lite and Full share that meaning; only their storage
and projection mechanics differ.
