# Memorist OpenWebUI

Memorist OpenWebUI is a local-first memory engine that runs beside Open WebUI. Open WebUI remains the parent chat product and owns the visible user experience; Memorist adds a separate local memory runtime that captures chat turns, builds evidence-grounded memories, retrieves relevant context, and injects bounded Memory Context Attachments through a server-side Open WebUI Filter.

The current line is `v0.2.0-beta.1 candidate` with Memory Intelligence Core Step 1, Full Mode Storage/Core Runtime Step 2, and Model Control Plane Runtime Integration Step 3 implemented. It is intended for local validation, daily-use testing, heavy-import verification, Open WebUI contract validation, model-role validation, source-package hygiene, and Full Mode preview testing. It is not a stable public release and must not be treated as Public Beta GO before the remaining release gates are complete.

```text
Open WebUI local chat
-> Memorist Filter parses inlet/outlet payloads
-> memorist-core resolves durable local session aliases
-> runtime profile selects SQLite Lite ledger or PostgreSQL Full ledger
-> hot Open WebUI writes enter the Lite write actor or Full hot scheduler/durable jobs
-> memory worker stores evidence-grounded local facts
-> retrieval builds a bounded Memory Context Attachment
-> filter inserts memory as separate untrusted context
-> original user/assistant text remains unchanged
-> model control records role-specific local usage/cost/privacy metadata
```

## Release State

- Package version: `0.2.0-beta.1`
- Storage schema version: `18`
- Release label: `v0.2.0-beta.1 candidate`
- Pinned Open WebUI smoke image: `ghcr.io/open-webui/open-webui:v0.9.6`
- RC zip: `release/rc/memorist-openwebui-0.2.0-beta.1.zip`
- RC checksum file: `release/rc/memorist-openwebui-0.2.0-beta.1.sha256`
- Package manifest: `release/package-manifest.ijson`
- Test classification manifest: `release/test_manifest.ijson`
- Open WebUI contract: `open-webui-integration/compatibility/openwebui_contract.md`

The package is scanned for forbidden files and secret-like content. Placeholder smoke scripts are classified as placeholders and do not certify release readiness. Real blocking local Lite/baseline gates include `make check`, `make model-control-tests`, `make memory-worker-prompt-pack-test`, `make openwebui-contract-tests`, `make smoke-daily`, `make smoke-import-heavy-ci`, `make heritage-roundtrip`, `make forget-residue`, `make consistency-check`, `make recovery-tests`, `make assemble-rc`, and `make rc-schema-test`. Full Mode has a separate certification command, `python scripts/full_mode_check.py`; skipped/manual Full gates block Full beta support but do not change the Lite baseline.

Current baseline status is intentionally split: Lite mode with SQLite is the supported local path; PostgreSQL canonical Full Mode, FalkorDB projection, hot scheduler, PostgreSQL job/outbox DDL, Lite-to-Full migration tooling, and external Full certification scripts are implemented, but the current claim remains `Full Mode: experimental preview; external certification incomplete.` Memory Worker Prompt Pack v2 is implemented with versioned, schema-bound prompts for preflight planning, Jakobson sentence analysis, routing assist, route-specific extraction, consolidation assist, block compaction, import reconstruction, contradiction detection, and privacy sensitivity. Sentence-level Jakobson annotations remain the primary semantic lens for memory routing, and the Model Control Plane enforces separate preflight, extraction, embedding, privacy, health, usage, and cost paths.

## Core Guarantees

- Local-first by default: SQLite is the Lite ledger; PostgreSQL is the Full ledger; FalkorDB is a rebuildable graph map, not source of truth.
- No telemetry, external analytics, cloud queue, or external server requirement in the default path.
- `MEMORIST_LOCAL_ONLY=false` is rejected.
- Open WebUI branding and UI ownership stay with Open WebUI.
- Main chat model selection remains owned by Open WebUI; Memorist only observes `main_chat_observed`.
- Preflight, memory extraction, privacy sensitivity, block compaction, import reconstruction, and embedding are separate Memorist model roles with independent defaults, privacy acknowledgement, and usage events.
- Memory-worker system prompts are versioned, local, output-validated, and tracked by prompt/model/input/output hashes.
- Memorist context is inserted separately from the user prompt.
- Retrieved/imported memory is treated as untrusted data.
- The integration is fail-open by default: chat continues if Memorist is unavailable.
- Session continuity is protected through alias resolution, not only Open WebUI `chat_id`.
- SQLite uses foreign keys, WAL, busy timeout, bounded retry, and a priority writer actor for hot writes, import commit, Heritage restore, and privacy mutation paths.
- Import is staged, throttled, resumable, cancellable, dry-run first, and committed in actor-batched low-priority chunks.
- Heritage export/verify/restore is validated by a rich roundtrip comparator covering canonical memory lineage, attachments, import metadata, privacy receipts, tamper detection, and FTS rebuild.
- Forget workflows include preview, confirmation, actor-backed execution, receipt, closure, and multi-layer residue checks across canonical memory, evidence, blocks, attachments, hot cache, FTS, and import payloads.
- Consistency, backup, and recovery commands are part of the release gate.
- Attachment budget is computed from model context, recent conversation size, safety margin, and configured mode.

## Repository Layout

```text
memorist-openwebui/
  memorist-core/
    pyproject.toml              uv project, FastAPI, Pydantic v2, pytest, ruff, mypy
    migrations/                 SQLite Lite migrations 0001..0018
    src/memcore/
      api/                      health, config, base APIs, retrieval, governance, import, budget, diagnostics
      openwebui/                durable session aliasing and idempotent message capture commands
      storage/                  SQLite Lite store, PostgreSQL Full store, canonical factory, migrations, write actor
      scheduler/                in-memory hot scheduler lanes, backpressure and status metrics
      graph/                    FalkorDB projection, graph diagnostics and rebuild command
      repositories/             domain repositories and atomic job claim
      memory_worker/            evidence-grounded memory pipeline and prompt registry
      model_control/            role defaults, provider abstraction, privacy/cost/usage/health tracking
      retrieval/                planning, lexical retrieval, ranking, semantic/graph extension points
      attachments/              dynamic budget, rendering, safety, attachment persistence
      active_memory/            Active Memory Blocks and compaction
      governance/               feedback, correction, privacy/forget workflows
      imports/                  staging, adapters, reconstruction, dry-run, progress, throttling, commit
      prompt_budget/            model registry and adaptive budget resolver
      heritage/                 export, verify, actor-backed restore, canonical compare
      eval/                     deterministic offline evaluation harness
      security/                 injection and policy checks
      performance/              local perf smoke and budgets
      reliability/              backup, consistency, recovery, and maintenance
    tests/                      reproducible uv-managed test suite

  open-webui-integration/
    compatibility/              explicit Open WebUI filter contract and payload fixtures
    memorist/
      filter/                   server-side Open WebUI Filter
      function/                 status Function
      shared/                   local client, parser, config, schemas, error handling
      tests/                    integration contract tests

  docs/                         architecture, daily-use, SQLite, import, preflight, diagnostics, deployment
  release/                      test manifest, scanner, package builder, smoke scripts, RC documents
  installer/scripts/            release manifest and RC assembly
  scripts/                      check and cleanup helpers
```

## Runtime Architecture

### Open WebUI Integration

The Filter lifecycle is documented in `open-webui-integration/compatibility/openwebui_contract.md`. The optional container-smoke target is pinned to `ghcr.io/open-webui/open-webui:v0.9.6`; automated release evidence remains contract-fixture based unless the operator explicitly runs the container smoke.

`inlet`:

1. parses Open WebUI payloads through `open-webui-integration/memorist/shared/payload_parser.py`;
2. extracts stable chat ID, temporary chat ID, client nonce, user ID, message ID, model metadata, and timestamp when available;
3. resolves or creates a Memorist session through `/memcore/openwebui/session/resolve`;
4. captures the user message through the SQLite write actor;
5. calls `/memcore/preflight`;
6. inserts a separate `memorist_context` message only when attachment generation succeeds.

`outlet`:

1. parses assistant response payloads;
2. de-duplicates repeated callbacks;
3. captures assistant output through the writer path;
4. links assistant response to the input message/attachment when possible;
5. fails open on local Memorist errors unless configured otherwise.

Unsupported payload shapes return warnings in metadata and do not crash Open WebUI.

### Model Control Plane

Memorist separates chat ownership from memory infrastructure:

- `main_chat_observed`: the model selected inside Open WebUI. Memorist records metadata only and never routes or blocks the main chat request.
- `preflight`: an optional bounded role before the main chat request. It runs after deterministic retrieval planning, validates `memorist.preflight_planning` output when a provider is configured, and fails open on timeout or invalid output.
- `memory_extraction`: an asynchronous post-response role. Assistant/user text is captured first, then extraction work is queued as a background job and uses the `memory_extraction` default, never the main chat model.
- `embedding`: a separate semantic indexing role. Lite mode can run without embeddings; changing the embedding default marks existing embedding records stale for re-indexing.
- `import_reconstruction`, `high_confidence_extraction`, `block_compaction`, and `privacy_sensitivity`: optional background roles for heavier workflows and sensitive-memory classification.

Profiles store provider metadata, endpoint locality, context/token hints, capabilities, cost profile, latency profile, quality profile, privacy profile, health events, and secret strategy. Raw API keys, tokens, passwords, and credentials are rejected; secrets are referenced by environment variable name only. Any non-local/external profile must be explicitly privacy-acknowledged before it can become a role default.

Safe beta defaults are local-first:

```text
Main Chat: selected in Open WebUI and observed by Memorist only
Pre-flight: deterministic_preflight, bounded, fail-open
Memory Extraction: deterministic_extraction, asynchronous
Embedding: disabled in Lite until a local embedding profile is configured
```

Example operator setup:

```text
Main Chat: Claude/GPT/Qwen through Open WebUI
Pre-flight: deterministic or qwen2.5:1.5b local
Memory Extraction: qwen2.5:3b local structured-output model
Embedding: nomic-embed-text local
```

Remote preflight, extraction, or embedding providers may receive selected snippets, sentence units, derived memory summaries, or embedding text depending on role settings. The privacy matrix shows the data categories before enabling the profile as a default.

Core endpoints:

```sh
curl http://localhost:8777/memcore/model-control/roles
curl http://localhost:8777/memcore/model-control/profiles
curl http://localhost:8777/memcore/model-control/defaults
curl http://localhost:8777/memcore/model-control/usage
curl http://localhost:8777/memcore/model-control/privacy
curl http://localhost:8777/memcore/model-control/health
curl http://localhost:8777/memcore/costs/model-roles
```

### Session Alias Resolution

Open WebUI can create chats before a stable final chat ID exists. Memorist stores durable aliases in `openwebui_session_aliases` and resolves sessions in this order:

1. stable Open WebUI conversation/chat ID;
2. client session nonce;
3. temporary chat ID;
4. first-message fingerprint;
5. legacy `sessions.openwebui_conversation_id` only when no user-specific alias is available;
6. new local session plus all available aliases.

When a stable chat ID appears later, it is attached to the existing Memorist session. Raw first-message text is not stored in the alias table; only hashes/fingerprints are persisted.

### Storage Profiles

Memorist has explicit runtime profiles:

```env
# Lite
MEMORIST_RUNTIME_PROFILE=lite
MEMORIST_CANONICAL_STORE=sqlite
MEMORIST_GRAPH_BACKEND=disabled
MEMORIST_HOT_SCHEDULER=disabled

# Full
MEMORIST_RUNTIME_PROFILE=full
MEMORIST_CANONICAL_STORE=postgres
MEMORIST_POSTGRES_DSN=postgresql://memorist:memorist@postgres:5432/memorist
MEMORIST_GRAPH_BACKEND=falkordb
MEMORIST_FALKORDB_URL=redis://falkordb:6379
MEMORIST_HOT_SCHEDULER=in_memory
```

Lite keeps SQLite as the canonical store and remains the default. Full refuses to start with SQLite as canonical store, requires a PostgreSQL DSN, checks PostgreSQL health before readiness, and requires FalkorDB unless explicit degraded mode is configured. FalkorDB is a graph projection from canonical PostgreSQL data; graph failure degrades retrieval/diagnostics but does not lose canonical data. The in-memory hot scheduler stores runnable references only; durable job and outbox payloads stay in PostgreSQL.

Diagnostics:

```sh
curl http://localhost:8777/memcore/health
curl http://localhost:8777/memcore/version
curl http://localhost:8777/memcore/diagnostics/daily
curl http://localhost:8777/memcore/scheduler/status
curl http://localhost:8777/memcore/graph/status
```

Full health/diagnostics include `runtime_profile`, `canonical_store`,
`graph_backend`, `graph_status`, `scheduler`, and
`full_mode_certification`.

### SQLite Write Discipline

Hot Open WebUI capture, import commit batches, Heritage restore, and privacy mutation commands are serialized through `SQLiteWriteActor` and `WriteGateway`. The actor owns one SQLite write connection, applies migrations, prioritizes commands, records queue metrics, rejects oversized write commands, counts busy retries, and supports idempotency replay for commands that opt in. Read paths use ordinary local SQLite connections.

The writer is pragmatic rather than dogmatic. Some bounded repository/admin write paths remain direct, but `make consistency-check` audits those locations and fails if a new direct write appears without a documented justification. The daily Open WebUI hot path and P2 heavy-write paths no longer compete across multiple request threads for the same write transaction.

Diagnostics:

```sh
curl http://localhost:8777/memcore/diagnostics/write-actor
curl http://localhost:8777/memcore/diagnostics/daily
```

### Memory Worker

The memory worker is evidence-grounded:

```text
message
-> text units
-> gate decisions
-> deterministic or structured analysis
-> candidate memories
-> evidence links
-> consolidation decisions
-> canonical memory versions
-> optional graph projection outbox
```

The included baseline is local and deterministic. The versioned prompt pack under `memorist-core/src/memcore/memory_worker/prompts/` now defines Prompt Pack v2 with `prompt_id`, `prompt_version`, input contract, output contract, evidence rules, rejection rules, allowed model roles, and timeout metadata for every non-chat prompt. `memorist.jakobson_sentence_analysis` v2.0 is the primary sentence-level semantic prompt; `memorist.unit_analysis` is retained only as a legacy derived summary. Specialized extractor prompts replace a generic extraction prompt: conative, referential, metalingual, emotive, and poetic routes produce evidence-grounded candidate inputs with `annotation_uuid` and `route_uuid`. Every prompt output uses the standard I-JSON envelope, is schema-validated before use, and can be audited in `prompt_execution_runs` with prompt/model/source hashes, raw/validated output references, status, warnings, latency, and token counts. The runtime resolves role-specific model defaults and never falls back to the Open WebUI Main Chat model for memory-worker prompts.

### Retrieval, Preflight, and Attachment

Preflight retrieval plans queries, selects candidates, applies scope/temporal/conflict rules, and renders a Memory Context Attachment. Attachments are persisted with source UUIDs, retrieval run UUIDs, token counts, and delivery attribution.

The attachment budget is adaptive:

```text
effective = min(
  configured max tokens,
  request cap,
  context window * ratio,
  context window - recent conversation estimate - reserved completion - safety margin,
  mode cap
)
```

Model context can come from the request, the local model registry, known defaults, or `MEMORIST_UNKNOWN_MODEL_CONTEXT_WINDOW`. If the effective budget is too small, attachment is disabled for that request rather than wasting context window.

### Active Memory and Governance

Active Memory Blocks are derived views, not the source of truth. Canonical memory versions remain authoritative. Governance workflows preserve auditability through feedback, correction, privacy preview/confirm/execute, receipts, and residue checks.

### Import, Heritage, and Recovery

Import is staged and non-destructive:

```text
archive upload
-> archive safety validation
-> adapter inspection
-> provider-neutral reconstruction
-> dry-run dedupe/cost report
-> actor-batched bounded commit with progress
-> post-import consistency check
```

Import progress supports pause, resume, cancel, throttling flags, failed-record counts, and current-batch reporting. Imported jobs are low priority by default so normal Open WebUI capture remains responsive.

The heavy-import smoke has three profiles: `ci-small` for release gating, `small-heavy` for local stress, and `local-heavy` for operator-only 10k-conversation testing. Executed profiles generate a synthetic Open WebUI ZIP, import it, re-import it for dedupe, capture a live Open WebUI message while import batches are queued, and run consistency checks. Skipped profiles are reported as skipped, not passed.

Heritage export creates portable, offline-verifiable packages with manifests, checksums, I-JSON/I-JSONL data, restore dry-run support, actor-backed restore, canonical database comparison, and checksum tamper rejection.

Backup and recovery use local SQLite in Lite. Full Mode stores durable jobs, outboxes and canonical data in PostgreSQL; FalkorDB can be rebuilt from PostgreSQL after recovery.

## Configuration

Copy `.env.example` to `.env` for local runtime use. `.env.example` is safe to package; `.env` is not packaged.

Important settings:

```env
MEMORIST_ENV=development
MEMORIST_LOCAL_ONLY=true
MEMORIST_HOST=0.0.0.0
MEMORIST_PORT=8777
MEMORIST_RUNTIME_PROFILE=lite
MEMORIST_CANONICAL_STORE=sqlite
MEMORIST_DB_PATH=./data/memorist.sqlite
MEMORIST_POSTGRES_DSN=
MEMORIST_OBJECT_STORE_PATH=./data/objects
MEMORIST_GRAPH_BACKEND=disabled
MEMORIST_FALKORDB_URL=
MEMORIST_VECTOR_BACKEND=disabled
MEMORIST_HOT_SCHEDULER=disabled
MEMORIST_PREFLIGHT_ENABLED=true
MEMORIST_PREFLIGHT_TIMEOUT_MS=1200
MEMORIST_PREFLIGHT_MODEL_TIMEOUT_MS=800
MEMORIST_PREFLIGHT_FAIL_OPEN=true
MEMORIST_RETRIEVAL_MODE=standard
MEMORIST_ATTACHMENT_MAX_TOKENS=1800
MEMORIST_ATTACHMENT_CONTEXT_RATIO=0.10
MEMORIST_ATTACHMENT_MIN_TOKENS=256
MEMORIST_ATTACHMENT_RESERVED_COMPLETION_TOKENS=1024
MEMORIST_UNKNOWN_MODEL_CONTEXT_WINDOW=8192
MEMORIST_IMPORT_BATCH_SIZE=100
MEMORIST_IMPORT_BATCH_MAX_MS=250
MEMORIST_SCHEDULER_MAX_CONSECUTIVE_LOW_PRIORITY=1
MEMORIST_HOT_LANE_TARGET_WAIT_MS=100
MEMORIST_PREFLIGHT_PERSIST_MAX_WAIT_MS=250
MEMORIST_IMPORT_MAX_JOBS_PER_MINUTE=60
MEMORIST_IMPORT_MAX_WRITE_QUEUE_DEPTH=500
MEMORIST_IMPORT_LOW_PRIORITY=true
MEMORIST_FAIL_OPEN=true
```

Provider credentials belong in Open WebUI provider settings, not in Memorist config files or release packages. The effective config endpoint redacts secret-like keys.

If Memorist needs to call a local or OpenAI-compatible memory model later, store the secret outside SQLite and pass only the environment variable name in the model profile. Non-local endpoints require an explicit privacy acknowledgement before default assignment.

```sh
curl http://localhost:8777/memcore/config/effective
```

## Development Setup

Requirements:

- Python `3.12`
- `uv`
- Docker/Compose only for local package runs

Run the API:

```sh
cd memorist-core
uv sync --all-extras --dev
uv run uvicorn memcore.main:app --host 0.0.0.0 --port 8777 --reload
```

Health and metadata:

```sh
curl http://localhost:8777/memcore/health
curl http://localhost:8777/memcore/version
curl http://localhost:8777/memcore/config/effective
curl http://localhost:8777/openapi.json
```

## Daily-Use API Surface

Base local objects:

- `POST /memcore/workspaces`
- `GET /memcore/workspaces`
- `POST /memcore/projects`
- `GET /memcore/projects`
- `POST /memcore/sessions`
- `GET /memcore/sessions`
- `PATCH /memcore/sessions/{session_uuid}`
- `POST /memcore/messages`
- `GET /memcore/messages/{message_uuid}/lineage`

Open WebUI hot path:

- `POST /memcore/openwebui/session/resolve`
- `POST /memcore/openwebui/messages/capture`
- `GET /memcore/model-control/roles`
- `POST /memcore/model-control/profiles`
- `POST /memcore/model-control/defaults`
- `POST /memcore/model-control/privacy/acknowledge`
- `GET /memcore/costs/model-roles`
- `GET /memcore/openwebui/status`

Import control:

- `POST /memcore/imports/upload`
- `POST /memcore/imports/{import_run_uuid}/inspect`
- `POST /memcore/imports/{import_run_uuid}/reconstruct`
- `POST /memcore/imports/{import_run_uuid}/dry-run`
- `POST /memcore/imports/{import_run_uuid}/commit`
- `GET /memcore/imports/{import_run_uuid}/progress`
- `POST /memcore/imports/{import_run_uuid}/pause`
- `POST /memcore/imports/{import_run_uuid}/resume`
- `POST /memcore/imports/{import_run_uuid}/cancel`

Privacy forget:

- `POST /memcore/privacy/forget/preview`
- `POST /memcore/privacy/forget/{request_uuid}/confirm`
- `POST /memcore/privacy/forget/{request_uuid}/execute`
- `GET /memcore/privacy/forget/{request_uuid}/closure`
- `GET /memcore/privacy/forget/{request_uuid}/residue`
- `GET /memcore/privacy/forget/{request_uuid}/receipt`

Heritage:

- `POST /memcore/heritage/export`
- `GET /memcore/heritage/verify`
- `GET /memcore/heritage/inspect`
- `POST /memcore/heritage/restore`

Budget and diagnostics:

- `POST /memcore/budget/attachment`
- `GET /memcore/model-registry`
- `POST /memcore/model-registry`
- `PATCH /memcore/model-registry/{model_profile_uuid}`
- `GET /memcore/diagnostics/write-actor`
- `GET /memcore/diagnostics/daily`

## Validation

Use uv-managed commands. Do not rely on globally installed packages.

```sh
make check
make memory-worker-prompt-pack-test
make smoke-daily
make smoke-import-heavy-ci
make heritage-roundtrip
make forget-residue
make consistency-check
make recovery-tests
make openwebui-contract-tests
make assemble-rc
make rc-schema-test
make p2-check
```

Equivalent commands from `memorist-core/`:

```sh
uv sync --all-extras --dev
uv run ruff check .
uv run mypy src/memcore
uv run pytest -q
uv run pytest tests/test_model_control_plane.py -q
uv run pytest tests/test_memory_worker_prompt_pack.py -q
uv run pytest ../open-webui-integration/memorist/tests -q
```

Release validation:

```sh
make release-check
python installer/scripts/assemble_rc.py
python -m release.scan_forbidden_files release/rc/memorist-openwebui-0.2.0-beta.1.zip
cd memorist-core && uv run python ../release/tests/rc_package_schema.py
python -m release.tests.report --manifest release/test_manifest.ijson --external-gates-passed
```

## Local Package Run

Build or refresh the RC package:

```sh
python installer/scripts/assemble_rc.py
```

Verify and extract:

```sh
cd release/rc
sha256sum -c memorist-openwebui-0.2.0-beta.1.sha256
unzip memorist-openwebui-0.2.0-beta.1.zip
cd memorist-openwebui-0.2.0-beta.1/release/memorist-openwebui
cp .env.example .env
```

Start Lite mode:

```sh
scripts/start-lite.sh
```

Open:

- Open WebUI: `http://localhost:3000`
- Memorist Core health: `http://localhost:8777/memcore/health`

Full compose mode is optional and experimental. It starts PostgreSQL and FalkorDB and configures PostgreSQL as the Full canonical store:

```sh
scripts/start-full.sh
```

Lite mode is the default RC path. Full remains experimental until `python scripts/full_mode_check.py` reports every required external gate as passed. To run individual external gates without compose, provide `MEMORIST_TEST_POSTGRES_DSN` and, for graph gates, `MEMORIST_TEST_FALKORDB_URL`. The compose certification gate starts `docker-compose.full.yml` automatically when Docker is available; set `MEMORIST_FULL_COMPOSE_SMOKE=false` only when intentionally skipping it. Change demo credentials before any non-local testing.

## Open WebUI Installation

Use these files:

- Filter: `open-webui-integration/memorist/filter/memorist_memory_filter.py`
- Function: `open-webui-integration/memorist/function/memorist_status_function.py`
- Contract fixtures: `open-webui-integration/compatibility/payload_fixtures/`
- Compatibility notes: `docs/openwebui-compatibility.md`

Filter Valves:

- `memorist_core_url`: local Memorist Core URL, usually `http://localhost:8777` or `http://host.docker.internal:8777`
- `enabled`: enable capture and preflight
- `preflight_enabled`: enable/disable attachment insertion
- `fail_open`: keep `true` for normal chat safety
- `retrieval_mode`: `lite`, `standard`, `full`, or `debug`
- `token_budget`: per-filter maximum attachment token cap
- `timeout_ms`: local preflight timeout

The integration client rejects remote URLs and credentials in the Memorist Core URL.

Optional pinned container smoke:

```sh
make openwebui-container-smoke
cd memorist-core
uv run python ../release/tests/openwebui_container_smoke.py --run-containers
```

The default make target reports skipped because Filter installation in Open WebUI remains a manual account-level step. Use `--run-containers` only when Docker is available and you want to verify the pinned Open WebUI image starts with Memorist Core.

## Operational Commands

From `memorist-core/`:

```sh
uv run python -m memcore.eval run --dataset src/memcore/eval/fixtures/basic.ijsonl
uv run python -m memcore.performance perf-smoke --profile lite
uv run python -m memcore.reliability check
uv run python -m memcore.reliability.consistency check --db-path ./data/memorist.sqlite --json-output ./data/reports/consistency.ijson
uv run python -m memcore.reliability backup --out backup.sqlite
uv run python -m memcore.reliability recover --db-path ./data/memorist.sqlite
uv run python -m memcore.reliability wal-checkpoint
uv run python -m memcore.reliability secure-delete-check
uv run python -m memcore.storage.postgres parity-report
uv run python -m memcore.graph rebuild --store postgres
uv run python -m memcore.migrate sqlite-to-postgres --sqlite ./data/memorist.sqlite --postgres "$MEMORIST_POSTGRES_DSN" --dry-run
uv run python -m memcore.imports generate-heavy ./data/openwebui-heavy.zip --conversations 1000 --messages-per-conversation 2 --branches 2
uv run python -m memcore.imports inspect path/to/export.zip
uv run python -m memcore.heritage verify path/to/heritage.zip
uv run python -m memcore.heritage restore path/to/heritage.zip --db-path ./data/restored.sqlite --dry-run
uv run python -m memcore.heritage compare path/to/heritage.zip --db-path ./data/source.sqlite --other-db-path ./data/restored.sqlite
```

## Packaging Rules

Do not share a raw development folder or ad-hoc ZIP of the working tree. Local working trees can contain `.git`, caches, virtualenvs, bytecode and SQLite databases. Use the source package builder for source upload:

```sh
python scripts/clean_artifacts.py --apply
python release/source_package.py --out release/source/open-webui-memorist-edition-source.zip
python -m release.scan_source_tree release/source/open-webui-memorist-edition-source.zip
```

The release package excludes:

- VCS metadata and CI metadata;
- Python caches and bytecode;
- local databases, WAL/SHM files, and runtime data directories;
- local `.env` files;
- build outputs, logs, coverage, and dependency caches;
- secret-like content detected by `release.scan_forbidden_files`.

The generated package manifest records each included file with path, size, SHA-256, and role.

## Known Limitations

- This is a beta candidate, not a stable release.
- Public Beta GO is blocked until Full Mode has real PostgreSQL/FalkorDB smoke evidence, graph retrieval evidence, graph forget-residue evidence, migration verification, and full compose evidence.
- Full Open WebUI version-matrix certification is still pending; the current contract is fixture-based.
- PostgreSQL canonical Full Mode and FalkorDB graph projection are implemented as experimental preview foundations, not beta-supported production paths. The required wording is `Full Mode: experimental preview; external certification incomplete.` unless `release/artifacts/full-mode-certification-report.ijson` shows all Full gates passed.
- Prompt Pack v2 is implemented for prompt governance, schema validation, and audit linkage; LLM-backed extraction providers remain opt-in behind explicit model profiles.
- Sentence-level Jakobson analysis is the primary semantic routing layer; `memorist.unit_analysis` remains an aggregate/legacy prompt.
- The SQLite writer actor protects hot Open WebUI, import commit, Heritage restore, and privacy mutation paths; bounded direct repository writes still exist and are audited.
- Import commit is actor-batched and progress-aware, but provider export formats can change without notice.
- Physical deletion is bounded by SQLite, WAL, filesystem, SSD, and backup behavior.
- Prompt injection cannot be eliminated; it is bounded through escaping, untrusted-data labeling, and tests.

## Key Documentation

- Architecture: `docs/architecture.md`
- Memory worker: `docs/phase-2-memory-worker.md`
- Prompt Pack v2: `docs/prompt-pack.md`
- Memory worker prompts: `docs/memory-worker-prompts.md`
- Prompt safety: `docs/prompt-safety.md`
- Daily use: `docs/daily-use.md`
- SQLite runtime: `docs/sqlite-runtime.md`
- Import operations: `docs/import.md`
- Heavy import readiness: `docs/heavy-import.md`
- Heritage roundtrip: `docs/heritage-roundtrip.md`
- Forget residue: `docs/forget-residue.md`
- Open WebUI compatibility: `docs/openwebui-compatibility.md`
- Consistency checker: `docs/consistency-checker.md`
- Backup and recovery: `docs/backup-restore.md`
- Preflight and budgets: `docs/preflight.md`
- Model control plane: `docs/model-control-plane.md`
- Model privacy: `docs/model-privacy.md`
- Model costs: `docs/model-costs.md`
- Diagnostics: `docs/diagnostics.md`
- SQLite heavy workloads: `docs/sqlite-heavy-workloads.md`
- Storage profiles: `docs/storage-profiles.md`
- Full mode: `docs/full-mode.md`
- PostgreSQL: `docs/postgres.md`
- FalkorDB: `docs/falkordb.md`
- Hot scheduler: `docs/hot-scheduler.md`
- SQLite to PostgreSQL migration: `docs/sqlite-to-postgres.md`
- Full certification report: `release/artifacts/full-mode-certification-report.md`
- Deployment guide: `docs/deployment-guide.md`
- Concept glossary: `docs/concept-glossary.md`
- Final four implementation plan: `docs/final-four-implementation-plan.md`
- Security: `docs/security.md`
- Open WebUI contract: `open-webui-integration/compatibility/openwebui_contract.md`
- Test manifest: `release/test_manifest.ijson`
- Known limitations: `KNOWN_LIMITATIONS.md`
