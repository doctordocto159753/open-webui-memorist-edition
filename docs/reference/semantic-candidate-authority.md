# Semantic candidate authority

Status: **WP02 interface freeze**
Contract freeze date: 2026-07-28
Verified base: `e5aac693a68a1176e61afaede6ae53d80a5a30fc`

This note is the canonical interface and stage-order contract for PRD-02 WP02.
Parallel work must consume these interfaces rather than creating local variants.
Operation 0 does not add a runtime, persistence schema, prompt, migration, or
candidate behavior.

The central authority boundary is:

```text
model              -> proposes what the current message means
deterministic code -> decides whether the proposal is admissible and what may persist
```

The model never writes a candidate or memory. It cannot invent or override a
route, gate, annotation, source authority, privacy classification, scope, or
candidate identity.

## Verified baseline

The base contains both required predecessors:

- PR #54, merged as `1342a638ee9603478fb39e9388a8def2f6d2a743`,
  leaves one `Consolidated CI` workflow with these four jobs:
  `Quality, Unit, Integration, and UI`;
  `PostgreSQL, Full Runtime, and FalkorDB`;
  `Package and Lifecycle`; and
  `One Deployment Product E2E`.
- PR #53, merged as the base commit, provides `TextEnvelope`,
  `validate_semantic_evidence`, and the final WP01 model/local authority
  boundary.

Operation 0 baseline versions and migration heads (historical freeze input):

| Surface | Current value |
| --- | --- |
| SQLite migration head | `0036_claim_polarity.sql` |
| PostgreSQL migration head | `0023_claim_polarity.sql` |
| Prompt pack | `memorist-memory-worker-prompt-pack-v2` / `2.0` |
| Active Jakobson prompt | `memorist.jakobson_sentence_analysis` / `3.0` |
| Active role manifest | `role-contract-manifest-v2` |
| Text envelope | `memorist.text.envelope.v3` |
| Segmentation | `memorist.text.segmentation.v2` |
| Referential hints | `memorist.text.referential.v2` |
| Evidence validation | `memorist.text.semantic_evidence_validation.v1` |
| Normalization | `memorist.text.normalization.v1` |

No WP02 migration number is reserved by this note.

The implemented WP02 baseline now uses SQLite head
`0037_semantic_coverage_audit.sql`, PostgreSQL head
`0024_semantic_coverage_audit.sql`, and `role-contract-manifest-v3`.
The table above is retained to make the contract-freeze provenance auditable;
it is not the current runtime/version status. Prompt Pack `2.0` and Jakobson
`3.0` intentionally remained unchanged.

## Actual runtime trace

### Lite

The durable worker enters through
`MemoryJobWorkerService._process_once_sqlite`; the synchronous API reaches the
same `MemoryWorkerPipeline`.

```text
captured Message
  -> MemoryWorkerPipeline.prepare_message
     -> current text-unit construction for provider input
     -> execute_jakobson_contract
     -> ProviderAttemptAuditRepository reserve/finalize when remote
  -> MemoryWorkerPipeline.process_message
     -> persist memory_processing_run
     -> persist text_units
     -> JakobsonAnalysisService.run_for_message
        -> persist prompt_execution_runs
        -> persist jakobson_analysis_runs
        -> persist jakobson_sentence_annotations
        -> SignalRouter
        -> persist memory_signal_routes
     -> persist memory_gate_decisions
     -> analyze only gate-eligible units with the legacy StructuredAnalyzer
     -> LiteCandidateAuthorityResolver re-reads gate, route, annotation, and run
     -> CandidateExtractor
     -> shared build_candidate_draft
     -> persist memory_candidates and candidate_evidence in SQLite
     -> privacy/high-confidence candidate stages
     -> MemoryConsolidator
     -> memory/version/evidence lineage
     -> embedding and graph projection boundaries
```

`discard` and `retain_raw_only` units are not sent through Lite's legacy
linguistic analyzer. `manual_review` units may be analyzed, but
`candidate_policy_for_gate_and_route` blocks automatic candidate creation.

### Full

The durable worker enters through `MemoryJobWorkerService.process_once`; the
synchronous API reaches the same `PostgresMemoryWorkerPipeline`.

```text
captured Message/job
  -> PostgresMemoryWorkerPipeline.prepare_message
     -> current text-unit construction for provider input
     -> execute_jakobson_contract
     -> ProviderAttemptAuditRepository reserve/finalize when remote
  -> PostgresMemoryWorkerPipeline.process_message
     -> persist memory_processing_run
     -> persist text_units
     -> persist prompt_execution_runs
     -> persist jakobson_analysis_runs
     -> persist jakobson_sentence_annotations
     -> persist canonical memory_signal_routes
     -> persist memory_gate_decisions
     -> legacy linguistic-analysis SQL adapter
     -> gated_candidate_adapter.record_candidates
        -> re-read persisted gates
        -> select a persisted authoritative route
        -> shared build_candidate_draft
        -> persist PostgreSQL memory_candidates and candidate_evidence
     -> privacy/high-confidence candidate stages
     -> PostgreSQL memory/version/evidence persistence
     -> embedding_outbox
     -> graph_projection_outbox
```

Full currently records a legacy `linguistic_analyses` row for every unit after
the gates are persisted; unlike Lite, it does not filter this legacy adapter to
gate-eligible units. Candidate creation remains gated because
`gated_candidate_adapter` re-reads the persisted gate and the shared candidate
policy fails closed.

### Shared authority and duplication

Shared semantic authority already exists in:

- `memory_worker.jakobson_runtime` for strict Jakobson execution, one repair,
  fallback, and provider-attempt audit;
- `memory_worker.semantic.authority` for authoritative route selection;
- `memory_worker.semantic.gate_policy` for analysis/candidate/memory permission;
- `memory_worker.semantic.candidate_mapping` for the existing route-to-candidate
  mapping;
- `memory_worker.semantic.candidate_service` for the runtime-neutral draft;
- `memory_worker.semantic.provenance_policy` for source ceilings; and
- `memcore.textsemantics` for the deterministic envelope and evidence integrity.

Lite and Full duplicate orchestration, SQL/repository adaptation, prompt-run
recording, legacy linguistic-analysis persistence, candidate persistence, and
consolidation. Full writes memories directly while Lite uses
`MemoryConsolidator`; WP02 must not increase this semantic duplication.

Prompt executions are recorded in `prompt_execution_runs`. Contract-bound
remote calls reserve attempt 1 (`initial`) and, at most, attempt 2 (`repair`) in
`processing_provider_attempts` before network I/O. Each row is finalized after
the call; a worker loss leaves the durable state `unknown_completion`.

Current Lite and Full candidate UUIDs are randomly generated by their storage
adapters. WP02 proposals replace that behavior for new WP02 candidates with the
deterministic identity defined below. Historical candidate IDs do not change.

## Frozen stage order

The runtime insertion order for WP02 is:

```text
existing immutable capture
  -> TextEnvelope v3
  -> current text units
  -> Jakobson v3
  -> persisted canonical route
  -> persisted deterministic gate
  -> bounded-context resolver
  -> semantic_candidate_analysis v1
  -> strict typed/schema validation
  -> WP01 evidence validation
  -> deterministic coverage planner
  -> existing candidate service
  -> canonical candidate persistence
  -> consolidation
```

The current pipelines make this safer than the older architecture sketch that
placed semantic analysis before gate: both already persist route and gate before
their candidate adapters.

The invariant is exact:

1. `discard` and `retain_raw_only` do not call semantic candidate analysis and
   cannot create a proposal.
2. `manual_review` may call semantic analysis for review evidence but cannot
   create an automatic proposal.
3. `analyze` and `analyze_high_confidence` may proceed, subject to persisted
   route, privacy, provenance, and sensitivity policy.
4. Every adapter re-reads the persisted gate and route immediately before
   candidate persistence. A model-returned gate or route field is forbidden.
5. A gate or route change invalidates the coverage plan and proposal identity.

Strict structure, semantic binding, and evidence integrity share the existing
single repair budget. The sequence is:

```text
parse
  -> strict Pydantic/JSON Schema validation
  -> deterministic semantic binding checks
  -> WP01 evidence validation
  -> one bounded repair if the budget is still unused
  -> repeat all validation once
  -> fail-closed fallback or evidence-admissible subset
```

There is no independent evidence retry loop. A record that remains invalid is
represented by coverage reasons; it is never reconstructed by local guessing.

## Field classification

The interface tables use these labels:

- **Model**: returned by the semantic model.
- **Deterministic**: created, bound, validated, or selected by local code.
- **Persistence**: the canonical destination once later operations implement it.
- **Authority**: whether the field may directly authorize a candidate decision.

“Validated proposal” means the model supplies meaning, but the value remains
non-authoritative until strict/evidence validation and every deterministic
ceiling pass.

## `SemanticAnalysisV1`

Strict contract identity:

```text
schema version: 1.0
prompt id:      memorist.semantic_candidate_analysis
prompt version: 1.0
stage:          semantic_candidate_analysis
model role:     memory_extraction
```

Every field is required. Extra fields are forbidden. The only analysis statuses
are `ok` and `abstain`; `retain_raw_only` and `needs_review` are deterministic
coverage outcomes, not model authority.

| Field | Type | Source/treatment | Persistence | Authority |
| --- | --- | --- | --- | --- |
| `schema_version` | literal `"1.0"` | Model echo; strict-bound | Prompt audit | Contract metadata only |
| `prompt_id` | fixed string | Model echo; strict-bound | Prompt audit | Contract metadata only |
| `prompt_version` | literal `"1.0"` | Model echo; strict-bound | Prompt audit | Contract metadata only |
| `status` | `ok \| abstain` | Model; closed enum | Prompt audit | Non-authoritative; fallback policy is local |
| `warnings` | list of strings | Model; sanitized and bounded | Prompt audit only | Explicitly non-authoritative |
| `semantic_units` | list of `SemanticUnit` | Model; strict and evidence validated | Validated prompt audit; referenced by coverage | Validated proposal only |
| `references` | list of `SemanticReference` | Model; membership/evidence validated | Validated prompt audit; accepted lineage only | Candidate list is non-authoritative |
| `relations` | list of `SemanticRelation` | Model; endpoint/evidence validated | Validated prompt audit; accepted lineage only | Validated proposal only |

`status=abstain` requires all three collections to be empty. `status=ok`
requires at least one structurally valid current-message unit before evidence
validation. If no unit survives evidence validation, the deterministic outcome
is `retain_raw_only`.

## `SemanticUnit`

| Field | Type | Source/treatment | Persistence | Authority |
| --- | --- | --- | --- | --- |
| `id` | non-empty string | Model; unique only within this output | Prompt audit and lineage | Explicitly non-authoritative; excluded from identity |
| `raw_start` | strict non-negative integer | Model; range/order validated | Coverage item and candidate evidence span | Evidence coordinate only |
| `raw_end` | strict positive integer | Model; range/order validated | Coverage item and candidate evidence span | Evidence coordinate only |
| `evidence` | string | Model; must equal current raw slice byte-for-byte | Existing prompt/candidate evidence records | Evidence only |
| `proposition` | string | Model; no local rewriting or spelling correction | Prompt audit; candidate object only after policy | Validated proposal only |
| `unit_type` | closed enum | Model; strict-bound | Coverage/candidate metadata | Validated proposal only |
| `durability` | closed enum | Model; strict-bound; planner may downgrade | Coverage/candidate metadata | Never overrides gate/policy |
| `polarity` | closed enum | Model; evidence validated | Candidate/version when a proposal is accepted | Validated proposal only |
| `epistemic_status` | closed enum | Model; strict-bound | Candidate metadata/coverage audit | Validated proposal only |

Closed enums:

```text
unit_type:        statement | instruction | question | explanation
durability:       durable | transient | context_only | unknown
polarity:         affirmed | negated | unknown
epistemic_status: asserted | hedged | hypothetical | questioned | unknown
```

These axes deliberately do not create a universal candidate ontology.
Candidate type, subject, and predicate remain outputs of the existing
authoritative route mapping. A question is not a durable user fact merely
because it is well-formed. `hedged` is not negation and does not change the
existing confidence formula.

Semantic units must be ordered by `(raw_start, raw_end)`, non-overlapping, and
fully contained in the current raw message. A unit spanning incompatible
current text-unit, gate, or route boundaries cannot be assigned one authority
record and therefore becomes `needs_review` or `unsupported`.

## `SemanticReference`

Referent IDs use a discriminated string namespace without introducing a second
target shape:

```text
current_unit:<SemanticUnit.id>
prior_context:<BoundedContextItem.context_item_id>
```

| Field | Type | Source/treatment | Persistence | Authority |
| --- | --- | --- | --- | --- |
| `id` | non-empty string | Model; unique within output | Prompt audit/lineage | Explicitly non-authoritative; excluded from identity |
| `source_unit_id` | current unit ID | Model; membership validated | Accepted lineage | Binding only |
| `marker_start` | strict integer | Model; current-message range validated | Coverage/lineage offsets | Evidence coordinate only |
| `marker_end` | strict integer | Model; current-message range validated | Coverage/lineage offsets | Evidence coordinate only |
| `marker_evidence` | string | Model; exact current raw slice | Existing prompt audit; lineage references offsets/hash | Evidence only |
| `status` | `resolved \| ambiguous \| unresolved` | Model; closed enum | Accepted lineage | Validated proposal only |
| `candidate_referent_ids` | non-empty/empty list as constrained below | Model; every ID must be supplied | Prompt audit | Explicitly non-authoritative candidate set |
| `selected_referent_id` | string or null | Model; membership and status validated | Accepted lineage | Validated proposal only |

For `resolved`, candidates are non-empty and the selected referent is present in
them. For `ambiguous` or `unresolved`, `selected_referent_id` is null. No ID
outside the current output or supplied bounded manifest is accepted.

The WP01 validator currently validates current-unit reference membership. The
WP02 semantic binding layer must additionally validate the
`prior_context:` namespace against the exact supplied manifest; it must not
duplicate WP01 raw-slice checks.

## `SemanticRelation`

| Field | Type | Source/treatment | Persistence | Authority |
| --- | --- | --- | --- | --- |
| `id` | non-empty string | Model; unique within output | Prompt audit/lineage | Explicitly non-authoritative; excluded from identity |
| `relation_type` | closed enum | Model; strict-bound | Accepted lineage | Validated proposal only |
| `source_unit_id` | current unit ID | Model; membership validated | Accepted lineage | Binding only |
| `target_referent_id` | discriminated referent ID | Model; supplied-endpoint validated | Accepted lineage | Validated proposal only |
| `evidence_start` | strict integer | Model; current-message range validated | Lineage offsets | Evidence coordinate only |
| `evidence_end` | strict integer | Model; current-message range validated | Lineage offsets | Evidence coordinate only |
| `evidence` | string | Model; exact current raw slice | Prompt audit; lineage by offsets/hash | Evidence only |

Closed relation enum:

```text
ratifies | corrects | supersedes | contradicts | elaborates
```

A `ratifies` or `corrects` relation to assistant context authorizes nothing by
itself. User authority requires a current user message, exact current-user
evidence, one uniquely resolved in-window target, and successful gate, route,
privacy, provenance, and sensitivity policy.

## `BoundedContextItem`

The resolver is deterministic and may query only canonical capture tables:
`messages`, `text_units`, `sessions`, and `memorist_session_actors`.
It performs no retrieval, embedding, graph, workspace-wide, global-memory, or
cross-session search.

| Field | Type | Source/treatment | Persistence | Authority |
| --- | --- | --- | --- | --- |
| `context_item_id` | deterministic UUIDv5 | Local canonical identity | Prompt input/audit manifest | Binding only |
| `user_uuid` | string | Trusted session-actor binding | Audit manifest by ID | Scope boundary |
| `session_uuid` | string | Canonical session | Audit manifest by ID | Scope boundary |
| `workspace_uuid` | string or null | Canonical session/actor | Audit manifest by ID | Scope boundary |
| `project_uuid` | string or null | Canonical session | Audit manifest by ID | Scope boundary |
| `message_uuid` | string | Canonical message | Audit manifest by ID | Lineage |
| `message_version_uuid` | string or null | Canonical version when present | Audit manifest by ID | Lineage |
| `text_unit_uuid` | string | Canonical text unit | Audit manifest by ID | Lineage |
| `role` | `user \| assistant \| tool` | Canonical message role | Audit manifest | Source ceiling input |
| `turn_index` | integer | Canonical message order | Audit manifest | Ordering only |
| `unit_index` | integer | Canonical unit order | Audit manifest | Ordering only |
| `raw_start` | integer | Canonical unit span | Audit manifest | Evidence coordinate only |
| `raw_end` | integer | Canonical unit span | Audit manifest | Evidence coordinate only |
| `text` | string | Exact runtime context supplied to model | **Not copied into a new audit table** | Untrusted context only |
| `raw_text_hash` | SHA-256 string | Deterministic over exact text | Audit manifest | Integrity only |
| `source_authority_ceiling` | closed enum | Local provenance policy | Audit manifest/lineage | Hard ceiling |

`source_authority_ceiling` is one of `user_explicit`, `assistant_claim`, or
`tool_observation`. An assistant item never becomes a user fact. A tool item is
eligible only when canonical same-turn/session lineage proves it is related;
all unrelated tool output is excluded.

The context item ID is UUIDv5 over canonical I-JSON containing:

```text
context identity version
user/session/workspace/project IDs
message UUID
message version UUID, or message raw hash when no version row exists
text unit UUID
role and turn/unit order
raw span and raw-text hash
```

The current message is excluded. The baseline is the latest two eligible prior
units; `TextEnvelope.context_dependency_hints`, which are explicitly
non-authoritative, may expand this to the latest six. Items are returned in
ascending conversation order after selection.

Excluded without exception:

- another user, session, workspace, or project authority boundary;
- hidden, deleted, redacted, or non-visible content;
- system messages and hidden prompts;
- `memorist_context` messages;
- Memory Context Attachments or their rendered text;
- unrelated tool output; and
- provider keys, secrets, or configuration.

## `CoverageDisposition`

The closed enum is exactly:

```text
durable_candidate
context_only
transient_instruction
unresolved_reference
rejected_by_gate
needs_review
unsupported
```

The model never selects this enum. The deterministic planner assigns exactly
one disposition to every evidence-admissible semantic unit.

## `CoverageItem`

| Field | Type | Source/treatment | Persistence | Authority |
| --- | --- | --- | --- | --- |
| `coverage_item_id` | deterministic UUIDv5 | Local identity | Canonical coverage audit | Identity |
| `semantic_unit_id` | string or null | Local binding to accepted unit | Canonical coverage audit | Binding only |
| `raw_start` | integer | Accepted unit or uncovered material span | Canonical coverage audit | Evidence coordinate |
| `raw_end` | integer | Accepted unit or uncovered material span | Canonical coverage audit | Evidence coordinate |
| `disposition` | `CoverageDisposition` | Deterministic policy | Canonical coverage audit | Authoritative disposition |
| `reason_codes` | ordered, deduplicated strings | Deterministic policy | Canonical coverage audit | Explanatory policy evidence |
| `gate_decision_uuid` | string or null | Persisted gate lookup | Canonical coverage audit | Authoritative lineage |
| `route_uuid` | string or null | Persisted route lookup | Canonical coverage audit | Authoritative lineage |
| `proposal_id` | deterministic UUID or null | Local planner | Coverage audit/candidate link | Candidate idempotency |

Whitespace, punctuation, and formatting-only separators need no item. Material
TextEnvelope token/identifier spans omitted by the model are grouped into
deterministic contiguous spans with:

```text
semantic_unit_id = null
disposition      = unsupported
reason_codes     = ["uncovered_material"]
proposal_id      = null
```

Material spans skipped because their persisted gate is `discard` or
`retain_raw_only` use `rejected_by_gate`; no synthetic semantic meaning is
created for them.

## `CoveragePlan`

| Field | Type | Source/treatment | Persistence | Authority |
| --- | --- | --- | --- | --- |
| `coverage_plan_version` | fixed version string | Local constant | Canonical coverage audit | Contract metadata |
| `message_uuid` | string | Canonical message binding | Canonical coverage audit | Lineage |
| `raw_text_hash` | SHA-256 string | `TextEnvelope` binding | Canonical coverage audit | Integrity |
| `processing_run_uuid` | string | Canonical processing run | Canonical coverage audit | Replay boundary |
| `semantic_prompt_execution_uuid` | string or null | Persisted prompt run | Canonical coverage audit | Audit lineage |
| `semantic_contract_hash` | SHA-256 string | Typed prompt contract | Canonical coverage audit | Invalidation boundary |
| `status` | closed enum | Deterministic result | Canonical coverage audit | Plan outcome |
| `items` | list of `CoverageItem` | Deterministic and completeness checked | Canonical coverage audit | Complete disposition ledger |
| `warnings` | reason-code list | Deterministic | Canonical coverage audit | Audit-only |
| `coverage_hash` | SHA-256 string | Canonical I-JSON of plan excluding itself | Canonical coverage audit | Integrity/idempotency |

Plan statuses are:

```text
complete | abstain | retain_raw_only | needs_review
```

`abstain`, `retain_raw_only`, and `needs_review` create no memory by themselves.
Coverage persistence stores IDs, offsets, versions, hashes, dispositions, and
reason codes. It must not duplicate raw messages, evidence, propositions, or
prior-context text.

## `CandidateProposal`

A proposal is deterministic planner output, not a database row. Only a
`CoverageItem` with `durable_candidate` has a proposal, and it has exactly one.
Every other disposition has `proposal=null`.

| Field | Type | Source/treatment | Persistence | Authority |
| --- | --- | --- | --- | --- |
| `proposal_id` | deterministic UUIDv5 | Local identity | Eventual candidate UUID and coverage link | Idempotency identity |
| `semantic_unit_id` | string | Accepted-unit binding | Candidate metadata/coverage link | Binding only |
| `message_uuid` | string | Canonical message | Candidate/evidence lineage | Lineage |
| `text_unit_uuid` | string | Unambiguous containing unit | Candidate/evidence lineage | Authority mapping |
| `raw_start` | integer | Accepted evidence span | Candidate evidence | Evidence coordinate |
| `raw_end` | integer | Accepted evidence span | Candidate evidence | Evidence coordinate |
| `evidence` | string | Exact current-message slice | Existing candidate evidence only | Evidence only |
| `candidate_type` | existing `CandidateType` | Existing route mapping | Candidate row | Deterministic mapping |
| `subject_key` | string | Existing route mapping | Candidate row | Deterministic mapping; no v2 redesign |
| `predicate` | string | Existing route mapping | Candidate row | Deterministic mapping |
| `object_payload` | canonical object | Validated proposition adapted by mapping | Candidate row | Proposal content only |
| `normalized_text` | string | Existing candidate mapping | Candidate row | Deterministic representation |
| `polarity` | closed enum | Accepted semantic unit | Candidate/version | Validated proposal only |
| `epistemic_status` | closed enum | Accepted semantic unit | Candidate metadata/coverage lineage | Validated proposal only |
| `durability` | closed enum | Accepted semantic unit; policy confirmed | Candidate metadata/coverage lineage | Never overrides policy |
| `source_authority` | existing `SourceAuthority` | Deterministic provenance policy | Candidate row | Hard authority ceiling |
| `explicitness` | existing `Explicitness` | Deterministic provenance policy | Candidate row | Hard authority input |
| `privacy_ceiling` | existing sensitivity class | Deterministic sensitivity/privacy path | Candidate status/coverage audit | Hard ceiling |
| `status` | existing candidate status | Deterministic policy | Candidate row | Candidate lifecycle |
| `gate_decision_uuid` | string | Persisted gate | Candidate metadata/coverage audit | Authoritative lineage |
| `route_uuid` | string | Persisted route | Candidate evidence/metadata | Authoritative lineage |
| `annotation_uuid` | string | Persisted annotation | Candidate evidence/metadata | Authoritative lineage |
| `prompt_execution_uuid` | string | Persisted semantic prompt run | Candidate/version lineage | Audit lineage |
| `context_lineage` | IDs, hashes, offsets, relation/reference IDs | Deterministic accepted subset | Candidate metadata/coverage links | Lineage; no raw context copy |
| `reason_codes` | ordered, deduplicated strings | Deterministic policy | Candidate/coverage audit | Explanatory |
| `automatic_candidate_creation_allowed` | boolean | Gate/route/provenance/privacy conjunction | Coverage audit | Final candidate permission |

The model cannot downgrade `secret`, `sensitive`, or `privacy_review`.
Unsupported existing route mapping becomes `unsupported` or `needs_review`;
WP02 does not invent subject-key v2.

Assistant context can establish reference lineage, never `USER_EXPLICIT`
authority. A current user ratification may receive `USER_EXPLICIT` only when:

1. the current role is user;
2. exact current-user evidence explicitly ratifies or corrects;
3. the accepted relation is `ratifies` or `corrects`;
4. one `prior_context:` assistant target resolves uniquely inside the supplied
   manifest; and
5. gate, route, privacy, provenance, and sensitivity policy all allow it.

Lineage retains both current user and prior assistant message/version/unit IDs,
the reference marker offsets, relation, and semantic contract hash.

## Deterministic proposal identity

Identity version:

```text
memorist.semantic_candidate.proposal_identity.v1
```

Canonical identity material is an I-JSON object with these fields:

```text
identity_version
planner_version
message_uuid
raw_text_hash
semantic_contract_hash
semantic_unit_fingerprint
raw_start
raw_end
route_type
route_status
gate_decision
source_authority
coverage_disposition
```

`semantic_unit_fingerprint` is SHA-256 over canonical I-JSON containing the
unit's evidence span, evidence hash, proposition, unit type, durability,
polarity, epistemic status, and the accepted reference/relation closure.
Model-chosen unit/reference/relation IDs are replaced by their canonical target
fingerprints before hashing.

The accepted closure includes selected context target IDs and hashes when
meaning depends on bounded context. It does not include unselected candidate
referents.

The proposal ID algorithm is:

```text
digest = SHA-256(canonical-I-JSON(identity_material))
proposal_id = UUIDv5(URL_NAMESPACE,
                    "memorist:semantic-candidate-proposal:v1:" + digest)
```

Excluded from identity:

- timestamps and random values;
- processing, stage, prompt-execution, annotation, route, or gate UUIDs;
- provider response IDs, latency, token/cost data, and model profile metadata;
- warnings and model confidence hints;
- mutable UI fields; and
- model-chosen local record IDs.

Route/gate UUIDs remain lineage but are represented in identity by their
authoritative semantic values. The immutable message hash and processing
contract boundaries make a replay stable, while a contract, planner,
gate/route value, provenance, disposition, evidence, meaning, or accepted
context-target change produces a different proposal.

The same proposal ID is the eventual WP02 candidate UUID/idempotency key. A
retry with identical trusted inputs must return or link the existing candidate,
never create another.

## Prompt and certification freeze

Jakobson v3 remains immutable. WP02 adds:

```text
prompt id:      memorist.semantic_candidate_analysis
prompt version: 1.0
stage:          semantic_candidate_analysis
role:           memory_extraction
```

The prompt pack remains `2.0`; adding the separately versioned prompt does not
reinterpret historical pack or Jakobson outputs.

When prompt/certification work lands, the role manifest becomes
`role-contract-manifest-v3`. `memory_extraction` exposes the ordered bundle:

```text
bundle id: memory-extraction-contract-bundle-v1
1. memorist.jakobson_sentence_analysis @ 3.0
2. memorist.semantic_candidate_analysis @ 1.0
```

The bundle hash is SHA-256 over canonical I-JSON containing the bundle ID,
ordered prompt IDs/versions, and both typed contract hashes. Changing either
prompt, typed schema, or canonical contract hash makes existing certification
stale. One provider profile remains the user-visible `memory_extraction`
default, but it is current only after both exact runtime contracts certify.

Connectivity or generic JSON output is not certification. JSON-mode providers
remain subject to the same local strict schema and semantic validation as
providers supporting strict `json_schema`.

## Safe parallelization

After this note is committed, work may proceed in these independent tracks:

1. **Prompt and certification**: strict semantic output/input contract, prompt,
   renderer, validator, fallback, ordered role bundle, and certification.
2. **Bounded context and reference binding**: Lite/Full canonical context
   resolvers and supplied-ID validation, with no retrieval dependency.
3. **Coverage and identity**: pure runtime-neutral planner, completeness checks,
   privacy/provenance ceilings, and UUIDv5 identity.
4. **Lite/Full adapters and audit persistence**: additive parity schema,
   crash-safe proposal/candidate linking, replay, forget/export/residue support,
   and pipeline integration.

All tracks import or reproduce these exact field names, enum values, identity
material, and authority rules. Any desired contract change requires updating
this note first and invalidating the relevant contract/bundle hash.

Prompt work does not persist candidates. Planner work performs no model,
database, retrieval, embedding, Open WebUI, or FalkorDB calls. Persistence work
does not reinterpret semantic content. Adapter work cannot weaken persisted
gate/route, privacy, provenance, sensitivity, fencing, or canonical-store
authority.

## Non-goals

This freeze does not authorize canonical subject-key v2, selector or retrieval
redesign, attachment redesign, embeddings or graph semantic authority,
cross-session or workspace-wide reference resolution, a general coreference
engine, free-form model memory writes, confidence recalibration, historical
backfill, destructive migration, direct FalkorDB writes, a new `ModelRole`, or
UI redesign.

Lite SQLite and Full PostgreSQL remain canonical. FalkorDB remains a rebuildable
projection only.

## Operations 1-3 implementation boundary

The frozen semantic prompt/contract, ordered memory-extraction certification
bundle, pure deterministic coverage planner, and content-free coverage replay
stores are implemented. SQLite migration `0037_semantic_coverage_audit.sql`
and PostgreSQL migration `0024_semantic_coverage_audit.sql` add only audit
metadata, hashes, offsets, dispositions, and lineage IDs.

At the Operations 1-3 checkpoint these components were deliberately
disconnected from the Lite and Full pipelines. They did not create memories,
write FalkorDB, perform retrieval, or change canonical route/gate authority.

## Operations 4-5 runtime closure

Operation 4 adds the versioned adversarial corpus and an implementation-
independent test oracle described in
[`wp02-golden-corpus.md`](wp02-golden-corpus.md). Expected units,
dispositions, omissions, ambiguous references, and fixed proposal UUID vectors
are authored test data rather than snapshots generated by the implementation.

Operation 5 connects both canonical stores through exactly one runtime-neutral
`SemanticCandidatePlanningService`. Lite and Full call it only after their
Jakobson annotations, routes, and gates are persisted. Store adapters perform
SQL and audit mechanics; they do not contain semantic policy or an alternate
planner.

The connected order is:

```text
TextEnvelope v3
  -> Jakobson v3 with the resolved memory-extraction profile
  -> persisted annotation / route / gate
  -> bounded context resolution
  -> semantic candidate analysis v1 with the same profile
  -> strict binding and WP01 evidence validation
  -> deterministic coverage plan
  -> proposal adaptation
  -> in-transaction gate / route re-read
  -> deterministic candidate reservation and link
  -> existing consolidation and projection stages
```

The bounded-context resolver takes the most recent two eligible prior text
units, or six only when a non-authoritative `TextEnvelope` dependency hint
expands the window. It independently rechecks trusted actor, session,
workspace, project, turn order, latest immutable message version, exact unit
slice, visibility, deletion, redaction, role ceiling, and sensitivity.
System content, tool output, stale units, and cross-session or cross-scope
records are excluded. Runtime context text remains in its canonical source
records and is not copied into the coverage audit tables.

If any persisted unit has a terminal `discard` or `retain_raw_only` gate, the
message is failed closed before semantic model execution so gated material is
never sent to that stage. `manual_review` may be analyzed but the coverage
policy prevents automatic candidate creation. A deterministic or failed
semantic execution produces an auditable abstention/zero-candidate plan; it
never falls back into the legacy candidate extractor.

Remote provider attempts are still reserved before I/O. A completed semantic
prompt execution is replayed from its validated audit output. Proposal UUIDs
remain candidate UUIDs, candidate links are reserved before writes, and the
final SQLite/PostgreSQL transaction locks and re-reads the persisted
gate/route authority. Existing candidate/evidence rows from a recoverable
pre-link crash are verified and linked rather than duplicated.
A restart may reuse a completed plan only after all durable links are complete
and their persisted gate/route rows still authorize automatic creation;
authority mutation makes replay fail closed.
