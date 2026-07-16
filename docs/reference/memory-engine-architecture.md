# Memorist Memory Engine Architecture

This document explains the memory-engine architecture of **Open WebUI Memorist Edition**. It focuses on the memory engine itself: design philosophy, processing layers, data model, runtime flows, implementation techniques, trust and privacy boundaries, Lite/Full mode separation, and the architectural contributions introduced by this project.

This document is not meant to replace the root README. The root README introduces the repository, installation, tests, and release status; [MEMORY_MACHINE.md](../MEMORY_MACHINE.md) is the condensed walk-through of the same pipeline.

## Current status

Defensible current status:

```text
Version: 0.2.0-beta.1 development baseline
Schema version: 18
Lite Mode: beta-candidate
Full Mode: certified in the tested local Docker environment
Open WebUI integration: contract-tested; pinned container smoke pending/manual
Memory Intelligence Core: implemented baseline
Model Control Plane: implemented backend/runtime baseline
Prompt Pack v2: implemented contract baseline
```

Full Mode is not yet beta-supported. Until all external PostgreSQL, FalkorDB, graph retrieval, graph forget/residue, and `docker-compose.full` gates pass, Full Mode must be described as:

```text
Full Mode: certified in the tested local Docker environment.
```

## The problem the memory engine solves

LLMs lose continuity in long-running work. A user may spend weeks or months discussing a project, writing style, product decisions, team workflows, emails, research material, imported chat history, and personal preferences. In the next session, the model often starts from zero, or relies on shallow retrieval over ambiguous chunks.

The simple solution is to store all chats and summarize or embed them. That is not enough. Raw chat is not memory. In one conversation, one sentence may be an instruction, another may be a project fact, another may define a term, another may express frustration, and another may merely keep the conversation open. If all of that is stored as one vague chunk, the system cannot build accurate or trustworthy memory.

Memorist tries to transform raw conversation into memory with the following properties:

```text
precise
evidence-grounded
traceable
versioned
retrievable
forgettable
prompt-controllable
auditable
local-first
```

The goal is not to make the model “remember everything.” The goal is to decide what deserves memory, where it came from, which scope it belongs to, whether it was later corrected, whether it is sensitive, and how it should be safely used at response time.

## Architectural philosophy

### 1. A raw message is not memory; it is evidence

```text
Raw message is evidence, not memory.
```

When the user writes:

```text
The product team must not create a Jira item unless it is linked to a scored epic.
```

the sentence is first stored as evidence. It is not yet final memory. It may become a project policy. It may apply only to one team. It may later be corrected. It may only be valid in a certain project context. The system therefore records it in an evidence ledger before interpreting it.

### 2. The sentence is the communication unit

```text
Sentence is the communication unit.
```

Large chunks are not precise enough for memory intelligence. A paragraph may contain several different communicative acts. Memorist splits messages into sentence units so that each sentence can be analyzed, routed, and linked to evidence independently.

### 3. Jakobson analysis is the conversation parser

In code intelligence, parsers such as Tree-sitter expose the structure of code. In Memorist, sentence-level Jakobson analysis acts as the communication parser for conversation.

```text
Tree-sitter parses code structure.
Jakobson sentence analysis parses communication structure.
```

For each sentence, the system identifies six communication factors:

```text
sender_addresser
receiver_addressee
message
context_referent
code
contact_channel
```

It also classifies the dominant and secondary communicative functions:

```text
referential
emotive
conative
phatic
metalingual
poetic
```

This analysis is not memory yet. It is an annotation that guides memory extraction.

### 4. A memory candidate is not truth; it is a routed interpretation

```text
MemoryCandidate is a routed interpretation.
```

When a sentence is conative and its receiver is the Product Team, it may be routed to `workflow_policy` or `team_obligation`. The specialized extractor for that route produces a candidate. That candidate is still not final memory. It must pass trust, privacy, contradiction, and consolidation checks.

### 5. Final memory is a versioned, evidence-linked claim

```text
Memory is a consolidated, source-linked, versioned claim.
```

A final memory must link back to evidence. It must have version history. It must support correction, contradiction, retraction, and supersession. Blind overwrite is not acceptable.

### 6. Projections are not the source of truth

FTS, embeddings, active blocks, graph projections, and retrieval caches are projections. The canonical store is the source of truth.

```text
SQLite is the Lite ledger.
PostgreSQL is the Full ledger.
FalkorDB is the graph memory map.
```

In Lite Mode, SQLite is canonical. In Full Mode, PostgreSQL is canonical. FalkorDB is a rebuildable graph projection, not an independent source of truth.

### 7. Memory is data, not instruction

A Memory Context Attachment must not mutate the user prompt and must not behave as absolute system instruction. Memory is given to the model with provenance, scope, trust level, and privacy constraints.

```text
Memory is data, not instruction.
```

## High-level architecture

```text
Open WebUI
  |
  | server-side Filter inlet/outlet
  v
Memorist Core API
  |
  +--> Raw Evidence Ledger
  |
  +--> Sentence Unitization
  |
  +--> Jakobson Communication Analysis
  |
  +--> Memory Signal Routing
  |
  +--> Route-Specific Extraction
  |
  +--> Trust / Privacy / Injection Review
  |
  +--> Consolidation / Versioning
  |
  +--> Canonical Store
  |       Lite: SQLite
  |       Full: PostgreSQL
  |
  +--> Projections
  |       FTS
  |       Active Memory Blocks
  |       Embeddings, optional
  |       FalkorDB Graph, Full preview
  |
  +--> Retrieval / Preflight Planning
  |
  +--> Memory Context Attachment
  |
  v
Main Chat Model inside Open WebUI
```

Open WebUI remains the UI and main-chat owner. Memorist is a companion memory runtime beside Open WebUI, not a replacement for it.

## The two main system flows

### Memory ingestion flow

```text
User / Assistant / Import
-> raw capture
-> message/session/project resolution
-> sentence segmentation
-> Jakobson sentence analysis
-> memory signal routing
-> specialized candidate extraction
-> privacy/trust/injection review
-> consolidation
-> memory versions
-> projections/outbox
```

### Memory use flow

```text
New user message
-> session/project/workspace resolution
-> retrieval plan
-> candidate retrieval
-> scope/privacy/conflict filtering
-> attachment budgeting
-> Memory Context Attachment
-> Open WebUI main chat model
-> assistant response
-> post-response capture
-> async memory worker jobs
```

A system with ingestion only is an archive. A system with retrieval only is shallow RAG. Memorist connects both: memory construction and controlled memory use.

## Layer 1 — Open WebUI Filter boundary

### Role

Open WebUI is the parent UI. Memorist connects through a server-side Filter with two main lifecycle points:

```text
inlet / pre-send
outlet / post-response
```

At inlet time, the user message is captured, the session/project is resolved, retrieval/preflight can run, and a Memory Context Attachment may be prepared. At outlet time, the assistant response is captured and memory-worker jobs are enqueued.

### Critical rule

```text
User prompt remains byte-for-byte unchanged.
```

Memorist must not rewrite the user prompt. If memory is needed, it is added as a separate, auditable attachment.

### Implementation techniques

- Separate Open WebUI integration boundary.
- Shared client for calls to Memorist Core.
- Contract tests for Filter/Function behavior.
- Fail-open preflight behavior.
- Capture key / idempotency to prevent duplicate message capture.
- Strict distinction between user messages and assistant responses.

## Layer 2 — Evidence Ledger

### Role

Every input is first stored as raw evidence:

```text
workspace
project
session
message
role
raw_text
timestamps
source
model metadata
import source
capture key
```

This layer does not interpret. It records what entered the system, when, where, and in which context.

### Why it matters

Without an evidence ledger, memory cannot be trusted. If a memory exists but cannot be traced back to the source sentence, it cannot be audited, corrected, scoped, or forgotten reliably.

### Implementation techniques

- Stable UUIDs.
- Content hashes.
- Idempotent inserts.
- Explicit transaction boundaries.
- Canonical migrations.
- Residue-aware delete/quarantine paths.
- Source mapping for imports.

## Layer 3 — Sentence Unitization

### Role

The raw message is split into sentence units:

```text
text_units
  unit_type = sentence
  message_uuid
  sentence_index
  text
  char_start
  char_end
  stable hash
```

### Why sentence-level processing?

Memory signal is more precise at sentence level. Example:

```text
All tasks must be linked to a scored epic. Urgent bugs are an exception.
```

The first sentence is a policy. The second is an exception/update. If the two are processed as one generic chunk, the system may attach the policy without the exception.

### Implementation techniques

- Deterministic sentence segmentation.
- Offset preservation for evidence spans.
- Stable hashing for replay/idempotency.
- Language-aware but local-safe defaults.
- No required LLM dependency for basic segmentation.

## Layer 4 — Jakobson Communication Analysis

### Role

This layer records the communication structure of each sentence.

Conceptual output:

```json
{
  "sentence": "...",
  "six_factors": {
    "sender_addresser": "...",
    "receiver_addressee": "...",
    "message": "...",
    "context_referent": "...",
    "code": "...",
    "contact_channel": "..."
  },
  "dominant_function": "conative",
  "secondary_functions": ["referential"],
  "function_reason": "...",
  "confidence": "high"
}
```

### Why Jakobson?

Human memory is not only factual memory. Conversation contains instruction, expression, definition, emphasis, style, relational contact, requests, corrections, and disagreement. Simpler memory systems often flatten these into summaries. Memorist first builds communication structure.

### Mapping functions to memory

| Function | Memory meaning | Possible routes |
|---|---|---|
| conative | instruction/request/obligation | prompt_instruction, workflow_policy, team_obligation |
| referential | fact/process/report | project_context, process_fact, jira_configuration |
| metalingual | wording/definition/naming | terminology_rule, naming_rule, style_policy |
| emotive | preference/frustration/stance | user_preference, emotional_stance, quality_feedback |
| poetic | form/style/slogan/rhetoric | branding_style, rhetorical_pattern |
| phatic | contact/interaction continuity | ignore, or interaction preference if repeated |

### Implementation techniques

- Prompt Pack v2.
- Schema-bound I-JSON output.
- Validator for function/confidence/schema.
- Storage in `jakobson_analysis_runs`.
- Storage per sentence in `jakobson_sentence_annotations`.
- Lineage to `text_units`.
- Rejection of invalid output.
- No direct conversion from annotation to final memory.

## Layer 5 — Memory Signal Routing

### Role

Routing decides which extractor should handle each annotation.

Example:

```text
dominant_function = conative
receiver = AI
=> prompt_instruction / task_constraint

dominant_function = conative
receiver = Product Team
=> workflow_policy / team_obligation

dominant_function = metalingual
=> terminology_rule / naming_rule

dominant_function = emotive
=> user_preference / emotional_stance
```

### Why separate routing?

If a generic extractor processes every sentence, memory becomes inaccurate. Routing ensures that instructions, facts, terminology rules, emotional stances, and style preferences are processed with different prompts and schemas.

### Implementation techniques

- Deterministic route rules.
- Optional LLM routing assist.
- `memory_signal_routes`.
- Priority and confidence.
- Route UUID for evidence lineage.
- `manual_review` for ambiguity or sensitivity.
- Cross-scope route prevention.

## Layer 6 — Route-Specific Candidate Extraction

### Role

Each route goes to a specialized extractor:

```text
conative_instruction_extractor
referential_context_extractor
metalingual_policy_extractor
emotive_preference_extractor
poetic_style_extractor
```

The output is a MemoryCandidate, not final memory.

### Conative example

Sentence:

```text
The product team must not create an item unless it is linked to a scored epic.
```

Candidate:

```json
{
  "candidate_type": "team_obligation",
  "subject": "Product Team",
  "predicate": "must_not_add",
  "object": "item_without_scored_epic",
  "scope": "project",
  "obligation_strength": "mandatory",
  "evidence": [
    {
      "message_uuid": "...",
      "unit_uuid": "...",
      "annotation_uuid": "...",
      "route_uuid": "...",
      "quote": "..."
    }
  ]
}
```

### Implementation techniques

- Specialized prompts.
- Schema validation.
- Evidence-required outputs.
- Rejection reasons.
- Separate confidence and importance fields.
- Candidate evidence records.
- Audit link to `prompt_execution_runs`.
- No candidate accepted without quote/span/source.

## Layer 7 — Trust, Privacy, and Injection Review

### Role

Before consolidation, a candidate passes trust and safety filters.

Core questions:

```text
Is this actually the user’s statement?
Is it assistant speculation?
Was the content imported and therefore historical_untrusted?
Is there prompt injection inside the content?
Is the memory sensitive?
Is the scope project-level or global?
Should it be never_auto_attach?
```

### Assistant speculation example

Assistant says:

```text
You probably prefer shorter answers.
```

This should not become user memory.

User says:

```text
From now on, answer more briefly.
```

This may become a preference or prompt instruction.

### Implementation techniques

- `privacy_sensitivity` prompt role.
- Deterministic sensitivity checks.
- Remote provider privacy acknowledgement.
- Prompt-injection fixture tests.
- Trust downgrade for imported content.
- Sensitive-memory retrieval restrictions.
- No raw secrets in model profiles.
- Redaction in diagnostics.
- Non-content-bearing forget receipts.

## Layer 8 — Consolidation and Memory Versioning

### Role

Candidates are matched against existing memories and one consolidation operation is selected:

```text
ADD
REINFORCE
UPDATE
SUPERSEDE
CONTRADICT
RETRACT
NOOP
REJECT
MANUAL_REVIEW
```

### Why versioning?

Human memory changes. The user may correct a rule, update a preference, or add an exception. If memory is blindly overwritten, evidence and history are lost.

### Example

First:

```text
All tasks must be linked to a scored epic.
```

Later:

```text
Make an exception for urgent bugs.
```

The system should not delete the first memory. It should create a version, exception, or update relation.

### Implementation techniques

- `memory_versions`.
- Current-version pointer.
- `supersedes`, `contradicts`, `corrects`, `retracts` relations.
- Temporal validity.
- Scope-aware consolidation.
- Evidence preservation.
- Manual review for low confidence.

## Layer 9 — Canonical Storage

### Lite

Lite uses SQLite:

```text
SQLite canonical ledger
WAL
foreign keys
local object store
FTS
SQLite write actor
bounded retry
safe backup
maintenance commands
```

Lite is designed for local daily use and is the current beta-candidate path.

### Full

Full uses PostgreSQL:

```text
PostgreSQL canonical ledger
PostgreSQL migrations
durable jobs/outbox
FOR UPDATE SKIP LOCKED
hot scheduler runnable references
FalkorDB projection
SQLite-to-PostgreSQL migration
```

Full passed external certification in the tested local Docker environment.

### Critical rule

Full must not silently fall back to SQLite. If `MEMORIST_RUNTIME_PROFILE=full`, the system must require `MEMORIST_CANONICAL_STORE=postgres` and a valid `MEMORIST_POSTGRES_DSN`.

## Layer 10 — Projection Layer

Canonical memory is not enough for fast use. The system builds projections.

Projection types:

```text
FTS
active memory blocks
embedding records
retrieval cache
graph projection
attachment-ready summaries
```

### Active Memory Blocks

Active blocks are not the source of truth. They are derived views built from current memory versions and must retain source UUIDs.

### Embeddings

Embeddings are optional. When the embedding model changes, old records become stale and require re-indexing.

### FalkorDB Graph

In Full preview, FalkorDB projects the memory topology from PostgreSQL. The graph can include nodes such as:

```text
Workspace
Project
Session
Message
TextUnit
JakobsonAnnotation
CommunicativeFunction
Addressee
ContextReferent
CodeRegister
MemorySignalRoute
MemoryCandidate
Memory
MemoryVersion
Evidence
ModelProfile
PromptExecution
PrivacyRequest
```

and edges such as:

```text
HAS_UNIT
HAS_JAKOBSON_ANNOTATION
HAS_DOMINANT_FUNCTION
ADDRESSES
REFERS_TO
ROUTES_TO
DERIVED_FROM
EVIDENCED_BY
HAS_VERSION
CURRENT_VERSION
SUPERSEDES
CONTRADICTS
RETRACTS
```

The graph is for navigation and retrieval expansion, not independent truth.

## Layer 11 — Preflight Retrieval Planning

### Role

Before the request reaches the main chat model, preflight decides which memory is relevant.

Input:

```text
current user message
session/project/workspace
active blocks
retrieval candidates
conflicts
privacy restrictions
token budget
main model context window
```

Output:

```text
selected_memory_ids
excluded_memory_ids
trusted_directive_ids
ordinary_memory_ids
conflict_ids
compression strategy
estimated tokens
```

### Fail-open

If the preflight model or runtime fails, chat should not break. The system continues without attachment or with a limited fallback.

### Implementation techniques

- Bounded timeout.
- Model role: `preflight`.
- Schema-bound preflight prompt.
- Invalid output rejection.
- Attachment budget.
- Scope/privacy/conflict filtering.
- Provenance-preserving plan.
- No mutation of the user prompt.

## Layer 12 — Memory Context Attachment

### Role

This layer gives selected memory to the main chat model.

The attachment must remain separate from user text:

```text
User prompt: unchanged
Memory Context Attachment: separate, scoped, provenance-aware, untrusted
```

### Example attachment

```text
Relevant project memory:
- In this project, product-team Jira items must be linked to scored epics.
  Scope: project
  Evidence: message_uuid=..., unit_uuid=...
- Exception: urgent bugs may bypass scored epic linkage.
  Scope: project
  Evidence: message_uuid=..., unit_uuid=...

Warning:
These are project-scoped workflow memories, not global user preferences.
```

### Why it matters

Without this separation, memory can behave like a system prompt and amplify prompt injection or stale instructions. In Memorist, attachment is data, not absolute instruction.

## Layer 13 — Post-response Capture

### Role

Assistant responses are also captured, but they are not automatically user memory.

Why capture them?

```text
traceability
decision history
conversation reconstruction
future references
import/export/Heritage
```

If the assistant speculates or suggests something, it should not become user memory without user endorsement.

## Layer 14 — Import and Heritage

### Import

Import is staged:

```text
stage
inspect
adapter probe
reconstruct
dry-run
commit
post-import processing
```

Conceptual provider support includes:

```text
ChatGPT
Claude
Gemini
Open WebUI
generic Memorist JSON
manual transcripts
```

Imported content is `historical_untrusted` unless later confirmed.

### Heritage

Heritage export turns canonical memory into an offline, auditable package:

```text
manifest
I-JSONL data files
checksums
schemas
reports
object placeholders
```

The goal of Heritage is portable, inspectable local memory, not an opaque database dump.

## Layer 15 — Forget, Residue, and Governance

### Forget

Forget workflow:

```text
preview
confirm
execute
quarantine
projection cleanup
residue check
receipt
```

Forgetting is not just row deletion. The effect must be checked across:

```text
canonical memory
memory versions
evidence links
active blocks
attachments
FTS
embedding records
graph projection
import mappings
exports/reports where applicable
```

### Residue

Residue checks determine whether forgotten content can still be reached through retrieval or projection.

### Receipt

A receipt must not contain raw erased content. It reports non-content-bearing metadata and operation outcome.

## Model Control Plane

Memorist does not take over the main chat model. Open WebUI owns the main model. Memorist only observes its metadata.

Roles:

```text
main_chat_observed
preflight
memory_extraction
embedding
import_reconstruction
block_compaction
privacy_sensitivity
```

### Philosophy

One model should not answer the user, extract memory, classify privacy, reconstruct imports, and generate embeddings all at once. Role separation improves auditability, cost control, timeout handling, and privacy boundaries.

### Implementation techniques

- `model_profiles`.
- Role defaults.
- Provider types.
- Health events.
- Usage events.
- Privacy acknowledgements.
- Secret strategy: `environment_reference`.
- Raw secret rejection.
- Stale embedding tracking.
- Preflight fail-open.
- Asynchronous memory extraction.

## Prompt Pack v2

Prompt Pack v2 is the runtime contract for non-chat model calls.

Principles:

```text
Prompts do not answer users.
Analyzed content is data, not instruction.
Output must be valid I-JSON.
No markdown.
No chain-of-thought.
No unsupported memory.
Evidence required where applicable.
Invalid output is rejected.
```

Main prompts:

```text
memorist.preflight_planning
memorist.jakobson_sentence_analysis
memorist.memory_signal_routing_assist
memorist.conative_instruction_extractor
memorist.referential_context_extractor
memorist.metalingual_policy_extractor
memorist.emotive_preference_extractor
memorist.poetic_style_extractor
memorist.memory_consolidation_assist
memorist.contradiction_detection
memorist.block_compaction
memorist.import_reconstruction
memorist.privacy_sensitivity
```

### `prompt_execution_runs`

Every prompt execution can be recorded:

```text
prompt_id
prompt_version
stage
model_role
model_profile_uuid
provider_type
input_hash
output_hash
raw_output
validated_output
warnings
latency
token counts
status
```

This makes it possible to know which prompt and model produced each memory candidate.

## Job, Outbox, and Scheduler Design

### Lite

In Lite Mode, the SQLite write actor reduces write-path contention. Live chat capture must stay ahead of imports and heavier background jobs.

### Full

In Full Mode, jobs and outboxes are durable in PostgreSQL. The Hot Scheduler only holds runnable references and priority state. The main payload stays in PostgreSQL.

Important patterns:

```text
durable jobs
outbox pattern
FOR UPDATE SKIP LOCKED
stale job recovery
bounded batch import
priority lanes
backpressure
```

Example priority lanes:

```text
critical_privacy
live_chat_capture
preflight_persist
assistant_capture
memory_extraction
import_commit
import_reconstruction
graph_projection
embedding_index
block_rebuild
maintenance
```

The goal is to prevent heavy import or graph projection from starving live chat.

## Error Handling and Fail-Open Philosophy

Memorist should help the chat, not hold it hostage.

Fail-open paths:

```text
preflight unavailable
model timeout
invalid preflight output
graph backend down
embedding unavailable
memory worker backlog
```

In these cases, Open WebUI chat continues and diagnostics report the issue.

Paths that should not silently fail open:

```text
privacy erasure
secret storage
Full Mode canonical store mismatch
database migration corruption
forget residue failure
```

## Security Architecture

The memory engine assumes memory and imports may be hostile or polluted.

Main threats:

```text
prompt injection inside imported chats
delimiter attacks
scope expansion
tool-call attempts
secret exfiltration instructions
stale memory directives
assistant speculation becoming memory
sensitive memory auto-attachment
forgotten content residue
```

Controls:

```text
analyzed text is data
prompt output validation
I-JSON only
schema validators
privacy sensitivity routing
remote provider acknowledgement
secret redaction
no raw secret persistence
forget residue checks
package forbidden-file scans
source tree scans
```

## Testing Strategy

The test strategy has several levels:

```text
core tests
Open WebUI contract tests
Model Control tests
Prompt Pack tests
Jakobson pipeline tests
security tests
daily smoke
heavy import smoke
Heritage roundtrip
forget residue
consistency check
recovery tests
source package scan
RC package schema
version consistency
Full Mode external gates
```

Full Mode external gates include:

```text
PostgreSQL canonical smoke
PostgreSQL job/outbox concurrency
scheduler live-chat preemption
import under live traffic
FalkorDB projection
FalkorDB rebuild
graph retrieval
graph down fallback
graph forget/residue
SQLite-to-PostgreSQL migration
full compose smoke
```

All listed gates passed externally in the recorded local Docker certification.

## Implementation Techniques

### 1. Separation of concerns

The code is divided into clear boundaries:

```text
core API
storage
memory worker
prompt registry
model control
retrieval
import
heritage
Open WebUI integration
release tooling
```

Each boundary has a specific role. This prevents memory, prompts, storage, and UI from collapsing into one tangled subsystem.

### 2. Repository / Store abstraction

Lite and Full storage paths are separated through store abstractions. SQLite and PostgreSQL should expose compatible canonical behavior while supporting different operational capabilities.

### 3. Migration-first design

Data changes are introduced through formal migrations. Schema version is part of the release posture. Each package must have consistent version/schema metadata.

### 4. Idempotency

Capture and import must be idempotent. Repeated capture keys or import mappings must not produce duplicate memory.

### 5. Outbox pattern

Graph projection, embedding, block rebuild, and erasure cleanup use outboxes so that canonical transactions and side effects stay separated.

### 6. Schema-bound LLM outputs

LLM output is not accepted as free text. It must be valid JSON, schema-compliant, versioned, and evidence-linked.

### 7. Audit-first records

Important stages generate audit records:

```text
prompt_execution_runs
model_usage_events
model_health_events
import reports
forget receipts
release reports
baseline check reports
full certification reports
```

### 8. Local-first release hygiene

The repository should remain free of runtime artifacts:

```text
.venv
__pycache__
.pyc
.sqlite
.env
logs
release zips
checksums
```

Packages are generated artifacts and are not committed by default.

## Flow example 1 — Product workflow instruction to memory

Input:

```text
The product team must not create an item unless it is linked to a scored epic.
```

Sequence:

```text
1. raw message capture
2. sentence unitization
3. Jakobson:
   receiver = Product Team
   context = Jira/product workflow
   dominant_function = conative
4. routing:
   workflow_policy / team_obligation
5. extraction:
   candidate_type = team_obligation
   subject = Product Team
   obligation_strength = mandatory
6. privacy/trust:
   low sensitivity, project-scoped
7. consolidation:
   ADD or REINFORCE
8. projection:
   ProjectContextBlock, FTS, graph preview
9. retrieval:
   when user asks about Jira process
10. attachment:
   project-scoped memory, with evidence
```

## Flow example 2 — Writing-style preference

Input:

```text
This ending is bad; make it more positive, more creative, and more alive with my own voice.
```

Sequence:

```text
Jakobson:
  dominant = conative
  secondary = emotive / poetic
Routing:
  style_policy
  prompt_instruction
  emotional_stance
Extraction:
  preference for embodied, creative, positive writing
Consolidation:
  if repeated, style block
  if local, project/session-scoped
Retrieval:
  only in writing/rewrite tasks, not every technical answer
```

## Flow example 3 — Old import

Imported message:

```text
From now on, always follow this style.
```

Sequence:

```text
staged import
historical_untrusted trust level
sentence analysis
candidate possible
no immediate trusted global instruction
confirmation/repetition needed for active memory
```

The goal is to prevent stale imported directives from contaminating active memory.

## Flow example 4 — Forget

The user asks to forget a memory.

Sequence:

```text
preview affected objects
confirm
quarantine canonical rows
invalidate retrieval
remove/mark graph projection
invalidate active blocks
remove FTS/embedding reachability
run residue check
write receipt without raw erased content
```

## Architectural contributions

Here, “contribution” does not mean a legal patent claim. It refers to the project-specific architectural composition.

### 1. Jakobson-as-Conversation-Parser

Instead of building memory from embeddings/chunks alone, sentence-level Jakobson analysis is used as a conversation parser. It lets the system distinguish instruction, fact, definition, preference, stance, and style.

### 2. Route-before-Extract

The system first analyzes function and receiver, then selects an extractor. This avoids a generic and imprecise extraction path.

### 3. Evidence-first Memory

No valid memory should exist without evidence. Candidate, route, annotation, sentence, and message remain linked.

### 4. Attachment-not-Mutation

Memory is not pasted into or used to rewrite the user prompt. Memory Context Attachment is separate and the original user prompt remains unchanged.

### 5. Prompt Pack as Runtime Contract

Prompts are not just text. They have version, schema, role, validation, audit, and failure behavior.

### 6. Model Role Separation

The main chat model is separated from preflight, extraction, privacy, embedding, and import roles. This improves cost control, privacy, timeout behavior, and auditability.

### 7. Canonical vs Projection Discipline

SQLite/PostgreSQL are the source of truth. Graphs, embeddings, and blocks are projections. This prevents source-of-truth drift.

### 8. Forget as Cross-Projection Erasure

Forgetting is not row deletion. Retrieval, blocks, graph, embeddings, and receipts must be considered.

### 9. Heritage as Portable Memory Evidence

Memory is not trapped in a local DB. Heritage export makes it portable and auditable through manifests, checksums, and I-JSONL.

### 10. Full Certification Discipline

Full Mode is not certified by unit tests alone. It requires external gates. If Docker or DSNs are unavailable, gates skip honestly and do not count as pass.

## Comparison with simple RAG

| Topic | Simple RAG | Memorist |
|---|---|---|
| Processing unit | chunk | sentence unit |
| Semantics | embedding similarity | communication-aware routing |
| Evidence | often vague | mandatory lineage |
| Memory | summary/vector | versioned claim |
| Correction | overwrite/append | update/supersede/contradict/retract |
| Prompt use | context dump | bounded attachment |
| Privacy | often shallow | sensitivity, trust, forget residue |
| Graph | optional visualization | projection from canonical memory topology |
| Import | ingest text | staged, dry-run, historical_untrusted |
| Audit | limited | prompt/model/storage/release audit |

## Current limitations

The architecture is not complete. Current limitations:

```text
Full Mode external certification passed (11/11)
Open WebUI pinned container smoke pending/manual
semantic quality of Prompt Pack v2 needs real-world evaluation
Jakobson routing needs larger multilingual fixtures
Full graph retrieval needs external evidence
Full graph forget/residue needs real FalkorDB pass
Model Control UI needs polish
operator UX for memory review still needs work
```

## Important commands

Baseline:

```bash
python scripts/baseline_check.py
```

Lite smoke:

```bash
make smoke-daily
make smoke-import-heavy-ci
make heritage-roundtrip
make forget-residue
```

Prompt/Memory tests:

```bash
cd memorist-core
python -m uv run pytest -q tests/test_memory_worker_prompt_pack.py tests/test_jakobson_pipeline.py
python -m uv run pytest -q tests/test_model_control_plane.py
```

Full certification:

```bash
python scripts/full_mode_check.py
```

Clean:

```bash
python scripts/clean_artifacts.py --apply
python scripts/clean_artifacts.py --check
python scripts/scan_source_tree.py
```

## Summary

Memorist is not a chat-log storage system. It is a local-first memory engine that tries to transform raw conversation into memory that is trustworthy, versioned, evidence-grounded, retrievable, and forgettable.

The core architecture is:

```text
Raw message is evidence.
Sentence is the communication unit.
Jakobson analysis is the conversation parser.
Route is the processing decision.
Candidate is evidence-grounded interpretation.
Memory is a consolidated, versioned claim.
Projection is the map for memory use.
Attachment is controlled memory consumption at chat time.
```

The system is designed so the model does not start from zero in long-running work, while also preventing every piece of old conversation from becoming active truth. Memory should help, not pollute the prompt. It should provide context, not replace truth. It should be usable, not merely archived. And it should be forgettable, not an endless accumulation.


# Afterword: Inspirations, References, and Positioning

Memorist did not emerge in isolation. It was shaped through close work with Open WebUI, through comparison with recent memory and agent-context systems, and through a specific design need: to build a local-first, evidence-grounded memory engine for long-running human–LLM work.

This project is not affiliated with, endorsed by, or derived from the projects listed below unless explicitly stated by their own licenses or maintainers. They are acknowledged here as reference points, inspirations, adjacent systems, or important contrasts.

## Open WebUI

[Open WebUI](https://github.com/open-webui/open-webui) is the parent interface and integration target for this edition. Its local-first, extensible, self-hosted orientation shaped the decision to keep Memorist as a companion memory runtime rather than a replacement chat product.

Memorist follows this boundary deliberately:

```text
Open WebUI owns the chat interface and main model experience.
Memorist owns local memory capture, processing, retrieval, and attachment.
```

This separation preserves Open WebUI as the user-facing workbench while allowing the memory engine to evolve independently.

## Model Context Protocol

The [Model Context Protocol](https://github.com/modelcontextprotocol) influenced the broader idea that context, tools, and memory should be exposed through explicit, auditable interfaces rather than hidden prompt stuffing. Memorist is currently implemented around Open WebUI integration, but its architecture is intentionally compatible with a future tool-first or MCP-facing surface.

The MCP direction is especially relevant for future external tool surface such as:

```text
memorist_search_memory
memorist_get_project_context
memorist_trace_decision
memorist_explain_attachment
memorist_forget_memory
memorist_get_memory_graph
```

## Codebase-Memory MCP

[Codebase-Memory MCP](https://github.com/DeusData/codebase-memory-mcp) was an important comparative reference for graph-native local intelligence. Its focus is different: it indexes source code into a persistent structural graph for AI coding agents. Memorist focuses on conversation, project memory, user preferences, workflow rules, imported histories, and controlled prompt attachment.

The architectural lesson is still important:

```text
Codebase-Memory treats code structure as something that deserves a persistent graph.
Memorist treats conversation structure as something that deserves a persistent memory topology.
```

In that sense, Codebase-Memory sharpened the role of the Memorist Jakobson layer. If Tree-sitter can parse code structure for coding agents, sentence-level Jakobson analysis can parse communication structure for long-running human–LLM work.

## Letta / MemGPT

[Letta](https://github.com/letta-ai/letta), formerly MemGPT, is a major reference point for stateful agents and memory-aware agent design. It helped clarify the importance of treating memory as an operational substrate rather than a cosmetic chat-history feature.

Memorist differs in its product boundary. It is not primarily an autonomous agent platform. It is a local companion memory runtime for chat workbenches, with explicit separation between:

```text
main chat
preflight planning
memory extraction
embedding
privacy sensitivity
import reconstruction
block compaction
```

This role separation is central to Memorist’s audit and privacy model.

## Zep and Graphiti

[Zep](https://github.com/getzep/zep) and [Graphiti](https://github.com/getzep/graphiti) are important references for temporal and graph-oriented agent memory. They demonstrate that long-term memory is not only a vector-search problem; temporal relationships, entity links, and evolving context matter.

Memorist adopts a related but distinct discipline:

```text
Canonical memory lives in SQLite or PostgreSQL.
Graph memory is a projection.
FalkorDB is a rebuildable memory map, not the source of truth.
```

This distinction is central to Memorist’s forget/residue and rebuild logic.

## Mem0

[Mem0](https://github.com/mem0ai/mem0) is a relevant reference for production-oriented memory layers for AI agents. Its emphasis on remembering user preferences, adapting over time, and reducing repeated context work overlaps with Memorist’s problem space.

Memorist’s differentiator is its communication-first extraction pipeline. Rather than treating memory extraction as generic salience detection, Memorist starts with sentence-level communicative analysis, routes memory signals by function and receiver, and only then produces evidence-linked candidates.

## Cognee

[Cognee](https://github.com/topoteretes/cognee) is an adjacent open-source memory platform that combines ingestion, knowledge graph construction, and persistent agent memory. It is relevant as part of the broader movement toward graph-augmented context systems.

Memorist’s narrower focus is the transformation of conversational evidence into scoped, versioned, auditable memory for Open WebUI-oriented work. Its graph layer is one projection among several, not the whole memory system.

## What Memorist adds

The projects above informed the field of comparison, but Memorist’s architecture combines them with a specific memory philosophy:

```text
Raw messages are evidence.
Sentences are communicative units.
Jakobson analysis is the conversation parser.
Memory signal routing precedes extraction.
Candidates are interpretations, not truths.
Memories are versioned, evidence-linked claims.
Graphs, embeddings, FTS, and blocks are projections.
Attachments are controlled memory use, not prompt mutation.
Forget must include residue checks across projections.
```

The result is not a general claim that Memorist is “better” than these systems. It is a narrower claim: Memorist explores a local-first memory engine for human–LLM work where conversational meaning, evidence, scope, privacy, correction, and controlled prompt use are first-class architectural concerns.



