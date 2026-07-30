# Architecture

Memorist is a local-first memory engine running beside Open WebUI. Open WebUI
remains the parent chat product and owns the visible chat experience; Memorist
owns local capture, memory processing, retrieval, attachment rendering,
import/export, privacy workflows, and release hardening.

Design constraint: **a local user should own the memory, inspect it, export it,
and erase it without depending on a remote service.**

```text
Conversation events are evidence.
Evidence produces memory candidates.
Candidates become memories only after gate, routing, and consolidation.
Memories are retrieved through scoped, budget-aware retrieval.
Retrieved memory is attached as data, not as command.
```

## System components

```text
Open WebUI (parent chat UI)
  └─ Memorist Filter (server-side)
       ├─ inlet:  capture user message, run preflight, attach memory context
       └─ outlet: capture assistant response
  └─ Memorist backend router (/api/v1/memorist/*, authenticated)
       ├─ memory workflow toggle state (Memory On / Off)
       ├─ attachment display proxy (read-only, redacted)
       └─ memory node setup proxy (admin-only)

memorist-core (FastAPI, local)
  ├─ evidence ledger (SQLite in Lite, PostgreSQL in Full)
  ├─ priority write actor / write gateway
  ├─ worker pipeline (units → Jakobson → route/gate → semantic coverage → candidates)
  ├─ retrieval planner and Memory Context Attachment builder
  ├─ Model Control Plane (roles, profiles, env-var secret references)
  ├─ import / Heritage export / restore
  ├─ privacy and forget workflow
  └─ diagnostics, consistency, and recovery checks

Optional projections (always rebuildable, never the source of truth)
  ├─ FTS lexical index
  ├─ embeddings / vector index
  └─ FalkorDB graph projection (Full)
```

## Storage runtime split

Memorist has two explicit ledgers and one projection store:

```text
SQLite      is the Lite ledger (default, supported local path).
PostgreSQL  is the Full ledger.
FalkorDB    is the graph memory map — a rebuildable projection, never canonical.
```

Lite uses SQLite, SQLite FTS, and a serialized SQLite write actor. Full uses
PostgreSQL as the canonical source of truth with durable jobs/outboxes,
FalkorDB as a rebuildable projection, and an in-memory hot scheduler that
stores runnable references only. Graph, embeddings, active blocks, and
attachments are derived artifacts and must be rebuildable or invalidatable
from canonical records.

Deep dives: [SQLite runtime](reference/sqlite-runtime.md) ·
[PostgreSQL](reference/postgres.md) · [FalkorDB](reference/falkordb.md) ·
[storage profiles](reference/storage-profiles.md) ·
[Full Mode](reference/full-mode.md)

## Memory lifecycle

```text
message
→ message_version
→ session_event
→ TextEnvelope + sentence/text unit
→ Jakobson annotation
→ canonical route + gate decision
→ bounded semantic analysis + evidence validation
→ deterministic coverage plan + proposal reservation
→ memory_candidate (+ evidence links, trust/provenance policy)
→ consolidation decision
→ memory
→ memory_version
→ retrieval candidate
→ Memory Context Attachment
→ delivery/usage attribution
```

Raw evidence, model analysis, candidate claims, consolidated memories, and
delivered context are deliberately separate stages. A model-generated sentence
is never treated as truth too early. The full conceptual walk-through lives in
[MEMORY_MACHINE.md](MEMORY_MACHINE.md).

## The recall path

When a user sends a message through Open WebUI:

1. The Filter receives the inbound body; the payload parser extracts user,
   chat/session, selected model, and message content.
2. The session resolver maps temporary and stable Open WebUI chat IDs to a
   Memorist session.
3. The per-chat memory workflow state is checked. **Memory Off is a
   server-side consent ceiling**: the turn is not captured, no processing job
   is queued, and no retrieval or attachment happens.
4. With Memory On, the user message is captured unchanged, and Memorist
   calculates an attachment budget from the selected model's context window.
5. Retrieval plans are scoped by workspace/project/session and query intent.
6. Candidates come from active blocks, recent session state, FTS,
   semantic/vector index, and optional graph projection; ranking prefers
   current, scoped, high-confidence, evidence-backed memories, and conflicts
   are flagged rather than silently flattened.
7. The attachment builder renders selected memory as a separate, bounded
   **Memory Context Attachment** with provenance and source UUIDs.
8. The Filter inserts the attachment as separate context; the original user
   prompt is preserved unchanged. The chat UI shows a read-only, redacted
   "Memory used" display for the turn.
9. If Memorist fails or times out, chat **fails open** without memory.

### Why attachment is separate from user text

Memory can contain prompt-like or malicious text, so retrieved memory is never
trusted as instruction by default. Attachments are rendered with clear
boundaries, escaped delimiters, and metadata marking them as memory data.
Trusted directives are a separate category that must be promoted through
policy, not inferred from ordinary memory.

## Semantic processing path

The worker path is evidence-grounded and uses one shared Lite/Full semantic
orchestration service:

```text
message → TextEnvelope + sentence units → Jakobson v3
→ persisted canonical route → persisted gate (gate before candidate)
→ bounded same-authority context → semantic candidate analysis v1
→ strict schema + exact-evidence validation
→ deterministic coverage/disposition + proposal UUID
→ replay-safe candidate/evidence persistence
→ consolidation decisions → memory versions → projection outbox
```

Route and gate are persisted before `SemanticCandidatePlanningService` may
run. The shared service resolves at most two prior eligible text units (six
only for dependency hints), invokes the certified whole-message semantic
contract, validates exact evidence, plans complete material coverage, and
maps only `durable_candidate` dispositions into deterministic proposals.
Terminal gates do not invoke semantic analysis or candidate persistence.
The legacy linguistic analyzer remains auxiliary and is **not** semantic
authority.

Prompt Pack v2 remains the package baseline. The ordered
`memory-extraction-contract-bundle-v1` certification bundle binds Jakobson v3
and semantic candidate analysis v1; changing either typed contract or prompt
makes certification stale. All analyzed text is data, not instruction.
Executions are audit-linked in `prompt_execution_runs`,
`processing_stage_runs`, and `processing_provider_attempts`.

Deep dives: [memory intelligence core](reference/memory-intelligence-core.md) ·
[central processing walkthrough](reference/core-memory-processing-walkthrough.md) ·
[prompt pack](reference/prompt-pack.md) ·
[prompt safety](reference/prompt-safety.md) ·
[memory worker prompts](reference/memory-worker-prompts.md)

## Model Control Plane path

Memorist uses explicit model roles rather than a single global model setting:

- Open WebUI main request → `main_chat_observed` metadata only. Memorist never
  configures the main chat model.
- User message before chat → bounded `preflight` retrieval/attachment step →
  fail-open if unavailable.
- Assistant response after chat → queued `memory_extraction` job →
  evidence-grounded memory pipeline.
- Canonical memory/query text → optional `embedding` profile → stale records
  are marked when the embedding default changes.
- Sensitive candidate memory → optional `privacy_sensitivity` profile.
- Active memory sources → optional `block_compaction` profile.
- Historical import fragments → optional `import_reconstruction` profile →
  untrusted historical reconstruction only.

Every role has a safe local deterministic fallback; remote OpenAI-compatible
profiles are optional. External or non-local profiles require an explicit
privacy acknowledgement before they can become role defaults. Profiles
reference secrets **by environment-variable name only**; raw keys are rejected
by the API and never stored in SQLite/PostgreSQL or returned to the browser.

Every stage uses one scoped resolver (project → workspace → global →
documented inheritance → built-in fallback) and one invocation boundary.
Executions are idempotent and audit-linked in `processing_stage_runs`,
`prompt_execution_runs`, and `model_usage_events`; Lite and Full expose the
same effective-profile and processing-trace contract.

The first-run **Memory Setup** page (Settings → Memorist) is an admin-only
wizard over this contract: it shows per-role readiness, tests profiles with
real role-capability calls (`/v1/chat/completions`, `/v1/embeddings`,
JSON-mode probes), and assigns role defaults.

Deep dives: [model control plane](reference/model-control-plane.md) ·
[model costs](reference/model-costs.md) ·
[memory control contract](reference/memory-control-contract.md)

## Import, Heritage, and Forget

**Import** treats provider archives (ChatGPT, Claude, Gemini, Open WebUI,
generic) as historical evidence, not trusted memory: secure staging → adapter
detection → normalization → dry-run → dedupe → explicit commit → optional
reconstruction. Heavy import uses priority queues and backpressure so live
chat stays responsive. See [import](reference/import.md) and
[heavy import](reference/heavy-import.md).

**Heritage** export packages canonical local state into an offline-verifiable
ZIP (I-JSON manifests, I-JSONL data, checksums, reports). Restore preserves
canonical UUIDs and rebuilds derived indexes. See
[heritage roundtrip](reference/heritage-roundtrip.md) and
[backup/restore](reference/backup-restore.md).

**Forget** is a dependency workflow, not just deletion: preview → confirm →
quarantine → erase/redact canonical records → invalidate derived artifacts →
residue check → receipt without raw erased content. See
[forget residue](reference/forget-residue.md).

## Installer and release architecture

The public release path is a self-contained package
(`release/memorist-openwebui/`) driven by Docker Compose:

- `Memorist.cmd` / `Install-Memorist.ps1` — Windows-first guided setup
  (Docker detection, `.env` generation, optional local API-key capture,
  health checks, browser launch), plus start/stop/restart/logs/reset/uninstall
  scripts and bash equivalents.
- `compose.yml` as the common base plus `compose.lite.yml` and
  `compose.full.yml` overlays, with healthchecks, loopback-bound application
  ports, internal-only data services, and `.env`-driven configuration. The
  older root `docker-compose.release.yml` is source compatibility only, not a
  Full certification target.
- Checksums (`checksums.sha256`) and packaging scripts under `installer/` and
  `release/`.

Provider API keys entered during install are written only to the local
`.env`/container environment; the browser UI references them by variable name
(the PR5-C boundary). See [INSTALLATION.md](INSTALLATION.md) and
[SECURITY.md](../SECURITY.md).

## WP02 semantic candidate authority

After the persisted route and deterministic gate, one shared Lite/Full service
builds a two-unit context manifest (six only when non-authoritative dependency
hints occur), invokes `memorist.semantic_candidate_analysis` once for the whole
message, applies strict schema and exact-evidence validation, and produces one
coverage disposition per accepted unit. The model may propose semantics but
cannot choose route, gate, privacy, provenance, proposal identity, or
persistence. Assistant context remains `assistant_claim` unless a current user
uniquely references and explicitly ratifies or corrects it.

Durable proposals use deterministic UUIDv5 identities. SQLite migration `0037`
and PostgreSQL migration `0024` store content-free coverage/link audit rows;
raw evidence remains in canonical message/candidate evidence stores. Full
projects embeddings and graph state only through outboxes—FalkorDB is never
semantic authority.

The exact prompt/response sequence, including preflight before the main model
and assistant capture afterward, is documented in
[Walkthrough پردازش حافظه در موتور مرکزی](reference/core-memory-processing-walkthrough.md).

## Consolidated CI

The single `.github/workflows/ci-consolidated.yml` workflow runs on
GitHub-hosted runners with exactly four jobs:

1. Quality, Unit, Integration, and UI
2. PostgreSQL, Full Runtime, and FalkorDB
3. Package and Lifecycle
4. One Deployment Product E2E

The scripts reuse dependency installs, package builds, and the Product E2E
deployment; no per-feature workflow fan-out is authoritative. See
[DEVELOPMENT.md](DEVELOPMENT.md).

## Inspirations

Memorist adapts ideas rather than importing frameworks: Open WebUI
Filters/Functions (integration surface), event sourcing (auditability and
versioned memory), Graphiti-style temporal validity, Letta-style active memory
blocks as rebuildable projections, LangMem-style hot/warm/cold paths,
GraphRAG/LightRAG-style layered retrieval, HippoRAG-style optional associative
graph retrieval, MemX-style abstention when evidence is weak, OpenMemory-style
portability (Heritage), MemoryOS-style memory layers, MIRIX-style memory
taxonomy, and Jakobson's communication model as the annotation lens that routes
extraction. The full essay-form treatment is in
[reference/memory-engine-architecture.md](reference/memory-engine-architecture.md).
# Provider contract execution authority

Remote processing nodes execute against one frozen authority tuple: source and
content hash, processing run, requested/effective role, scope and inheritance,
profile fingerprint, prompt version, and contract hash. The worker commits an
append-only `processing_provider_attempts` reservation before each paid HTTP
call. A missing completion update is reported as `unknown_completion`; a retry
must not repeat that call. Lease, source, role, profile, or contract invalidation
is checked before the first call, after every response, and immediately before
the single bounded repair.

Stage outcomes use only `ok`, `abstained`, `failed_open`, and `failed`. Historical
spellings are normalized at read boundaries. Provider output is parsed,
validated, narrowly canonicalized (`success` → `ok` only), repaired at most
once, then replaced by a contract-valid deterministic fallback. Raw provider
content and credentials are never stored in attempt audit rows.
