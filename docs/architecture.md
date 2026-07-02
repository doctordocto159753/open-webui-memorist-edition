# Architecture

Memorist is a local-first memory engine running beside Open WebUI. Open WebUI remains the parent chat product; Memorist owns local capture, memory processing, retrieval, attachment rendering, import/export, privacy workflows, and release hardening. The current architecture has a supported SQLite Lite path and an experimental PostgreSQL/FalkorDB Full path.

## Phase 1 runtime

- `memorist-core` exposes a small FastAPI service.
- SQLite is the default Lite ledger.
- PostgreSQL is the Full ledger when `MEMORIST_RUNTIME_PROFILE=full`.
- FalkorDB is a rebuildable graph memory map, never the source of truth.
- Graph storage is configured as `disabled`.
- Open WebUI integration ships as a local Filter/Function package under `open-webui-integration/`.
- Logging is local stdout JSON and does not send telemetry.

## Extension surfaces

- Graph projection can remain disabled in Lite or target FalkorDB in Full. PostgreSQL canonical Full Mode and FalkorDB projection are implemented as Step 2 experimental preview foundations.
- Model-backed worker nodes can be assigned through explicit model roles.
- Open WebUI integration stays a local Filter/Function bundle.
- Import adapters can be added without changing canonical storage.

## Phase 2 worker path

The worker path is local and evidence-grounded:

message -> text units -> gate decisions -> structured analysis -> memory candidates -> evidence -> consolidation decisions -> memory versions -> graph projection outbox.

SQLite remains authoritative in Lite. PostgreSQL is authoritative in Full. Graph projection is optional, idempotent, and rebuildable.

Prompt Pack v2 is versioned under `memcore.memory_worker.prompts`. It defines `memorist.jakobson_sentence_analysis` v2.0 as the primary sentence-level semantic prompt, plus role-specific prompts for routing assist, conative/referential/metalingual/emotive/poetic extraction, consolidation assistance, preflight planning, block compaction, import reconstruction, contradiction detection, and privacy sensitivity classification. `memorist.unit_analysis` is retained only as a legacy derived summary and is not the primary graph-memory semantic layer. All analyzed text is data, not instruction. Model outputs must be valid I-JSON, use the global prompt envelope, satisfy prompt-specific schemas, include evidence where required, and pass validation before they can affect local state. The `prompt_execution_runs` ledger records `prompt_id`, `prompt_version`, model role/profile, provider, source scope, input/output hashes, raw/validated outputs, status, warnings, latency, and token counts.

## Phase 3 pre-send path

The retrieval path is local, bounded, and auditable:

current user message -> retrieval plan -> hybrid candidate generation -> deterministic ranking -> selection or abstention -> Memory Context Attachment -> Open WebUI filter injection.

Attachments are persisted with provenance and source UUIDs. Retrieved memory content is rendered as untrusted data unless it is an active approved constraint. The original user and assistant messages are preserved unchanged.

## Phase 4 governance path

Active Memory Blocks are derived views over canonical memories, not sources of truth. They are rebuilt from current canonical versions, approved policies, and session hot cache; previous block prose is never used as a source.

Governance tracks memory delivery separately from attribution: retrieved, selected, rendered, injected, used, and helpful are distinct states. User correction creates new versions or status changes instead of editing normal history in place. Privacy erasure uses preview/confirm/execute with dependency discovery, immediate retrieval quarantine, adapter cleanup, and non-content-bearing receipts.

## Phase 5 import and heritage path

Provider archives enter a secure staging area first. ZIP validation rejects unsafe paths, symlinks/devices, nested archives, and oversized or suspiciously compressed payloads. Adapters then probe ChatGPT, Claude, Gemini, Open WebUI, generic Memorist JSON, and manual transcript shapes before reconstructing provider-neutral conversation graphs.

Dry-run reports dedupe decisions and expected canonical writes before commit. Commit creates sessions/messages and import mappings only after explicit approval by the caller. Heritage export packages canonical local state into an offline-verifiable ZIP with I-JSON manifests, I-JSONL data files, checksums, schemas, object placeholders, and reports.

## Phase 6 hardening path

Evaluation is offline and deterministic. Golden I-JSONL fixtures score memory retrieval, temporal selection, abstention, scope isolation, and attachment budget behavior by canonical memory keys rather than generated prose.

Security hardening treats memory and imported text as untrusted data by default. Prompt-like content, delimiter attacks, scope-expansion attempts, tool-call attempts, and secret requests are flagged and escaped instead of deleted or promoted to directives.

Reliability hardening adds local consistency checks, safe SQLite backup through the SQLite backup API, WAL checkpoint/VACUUM/secure-delete maintenance commands, and release gates for tests, security, eval, performance smoke, migrations, import, and Heritage restore.

## Phase 7 package path

Open WebUI remains the parent UI. Memorist integrates through a trusted server-side Filter and status Function. The Filter captures messages into local Memorist Core, calls preflight, and inserts memory context as a separate untrusted system-like message without changing the original user prompt.

The local package provides Lite and experimental Full Compose profiles, explicit persistent volumes, doctor/backup/restore scripts, release smoke reports, checksums, and an RC zip. Lite is the supported baseline path. The Full compose profile starts PostgreSQL and FalkorDB, but remains experimental until external Full smoke evidence is recorded.

## Daily-use hardening path

The daily Open WebUI hot path is serialized through a SQLite write actor. Session resolution and message capture commands enter a single local writer queue, get idempotency replay when a capture key repeats, and expose queue/latency counters through diagnostics. Read APIs continue to use independent local SQLite connections.

Import remains an explicit operator workflow: stage, inspect, reconstruct, dry-run, commit. Commit is bounded by configurable batch size, records progress, supports pause/resume/cancel, and pauses on writer backpressure. Imported processing jobs are lower priority than live chat capture.

Model context for attachments is resolved from request metadata, local registry entries, known defaults, or a conservative fallback. Unknown models degrade to the configured fallback window instead of forcing a fixed token budget.

## Model Control Plane path

Memorist uses explicit model roles rather than a single global model setting:

Open WebUI main request -> `main_chat_observed` metadata only.

User message before chat -> bounded `preflight` retrieval/attachment step -> fail-open if unavailable.

Assistant response after chat -> queued `memory_extraction` job -> evidence-grounded memory pipeline.

Canonical memory/query text -> optional `embedding` profile -> stale records are marked when the embedding default changes.

Sensitive candidate memory -> optional `privacy_sensitivity` profile -> high-sensitivity memory is restricted or routed to manual review.

Active memory sources -> optional `block_compaction` profile -> compact derived block with preserved source UUIDs.

Historical import fragments -> optional `import_reconstruction` profile -> untrusted historical reconstruction only.

External or non-local profiles require privacy acknowledgement before use. Profiles reference secrets by environment variable name only; raw keys/tokens/passwords are rejected and redacted from API responses.

The Step 3 runtime integration adds health and usage enforcement to this path. Profile test calls write sanitized `model_health_events`; preflight model calls are bounded by `MEMORIST_PREFLIGHT_MODEL_TIMEOUT_MS`; invalid preflight prompt output is rejected and fails open; memory-worker processing records `memory_extraction` usage against the resolved role default; embedding records include profile tracking and stale/re-index state. Cost diagnostics are available through `/memcore/costs/model-roles`.

For operational deployment steps, see `docs/deployment-guide.md`.
