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
  ├─ memory worker pipeline (sentence units → Jakobson → gate → route → candidates)
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
PostgreSQL  is the Full ledger (advanced preview).
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
→ sentence/text unit
→ Jakobson annotation
→ canonical route + gate decision
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

The worker path is local and evidence-grounded:

```text
message → sentence units → Jakobson six-factor annotation
→ canonical semantic route selection → gate decision (gate before candidate)
→ route-specific candidate extraction → evidence + trust/provenance policy
→ consolidation decisions → memory versions → projection outbox
```

One canonical semantic authority produces routes, gates, and candidate
policies, shared by Lite and Full so both profiles make the same semantic
decisions. The complementary `StructuredAnalyzer` produces auxiliary
annotations and is **not** the semantic authority. Phatic/greeting-only turns,
privacy/forget requests, and manual-review paths do not create ordinary
memories.

Prompt Pack v2 defines versioned, schema-bound prompts for sentence analysis,
routing assist, route-specific extraction, consolidation assist, contradiction
detection, block compaction, import reconstruction, and privacy sensitivity.
All analyzed text is data, not instruction; model outputs must be valid I-JSON
and pass schema validation before they can affect local state. Executions are
audit-linked in a `prompt_execution_runs` ledger.

Deep dives: [memory intelligence core](reference/memory-intelligence-core.md) ·
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
- `compose.yml` with `lite` and `full` profiles, healthchecks, loopback-bound
  ports, and `.env`-driven configuration; the repo root carries an equivalent
  `docker-compose.release.yml` for source checkouts and CI validation.
- Checksums (`checksums.sha256`) and packaging scripts under `installer/` and
  `release/`.

Provider API keys entered during install are written only to the local
`.env`/container environment; the browser UI references them by variable name
(the PR5-C boundary). See [INSTALLATION.md](INSTALLATION.md) and
[SECURITY.md](../SECURITY.md).

## Certification workflows

CI protects the main contracts on every pull request:

| Workflow | Protects |
| --- | --- |
| Semantic Baseline | canonical semantic authority, gate/route/candidate parity, trust/provenance |
| Memory Control Contract | server-side memory control, scope isolation, actor authentication |
| Memory Attachment UX | read-only, redacted attachment display |
| Memory Workflow Toggle | truthful per-chat Memory On/Off consent ceiling |
| Memory Node Configuration | admin-only setup, secret redaction, provider contract |
| One-Click Installer | installer static checks, compose config, dry run, release manifest |
| Import Runtime Certification | import security, worker lifecycle, Full PostgreSQL import |
| Public Release Readiness | docs/link integrity, repo hygiene, core lint/type, frontend smoke |

Workflows run on self-hosted runners (`memorist-ci`); public forks may need to
adapt the `runs-on` labels. See [DEVELOPMENT.md](DEVELOPMENT.md).

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
