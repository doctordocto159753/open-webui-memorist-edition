# Open WebUI Memorist Edition

Local-first, evidence-grounded memory for Open WebUI conversations.

Memorist runs beside Open WebUI as a separate local memory runtime. Open WebUI remains the parent chat product and owns the visible user experience; Memorist captures chat turns, preserves evidence, builds versioned memories, retrieves relevant context, and returns bounded Memory Context Attachments through a server-side Open WebUI Filter.

This repository is a **v0.2.0-beta.1 development baseline**. It is ready for continued GitHub development and independent review. It is **not** a stable public release, and it is **not** Public Beta GO.

## Status

| Area | Status |
|---|---|
| Version | `0.2.0-beta.1` |
| Storage schema | `18` |
| Recommended label | `v0.2.0-beta.1 development baseline` |
| Lite Mode | Beta-candidate local path |
| Full Mode | Experimental preview; external certification incomplete |
| Open WebUI integration | Contract-tested; pinned container smoke pending/manual |
| Memory Intelligence Core | Implemented baseline; real-world semantic evaluation still needed |
| Model Control Plane | Implemented backend/runtime baseline; UI polish pending |
| Memory Worker Prompt Pack v2 | Implemented contract baseline; provider-quality evaluation pending |

Full Mode must not be described as beta-supported unless `python scripts/full_mode_check.py` reports all required external PostgreSQL, FalkorDB, graph retrieval, graph forget/residue, and compose gates as passed. Skipped or manual Full gates do not count as pass.

## What this project is

Memorist is a local memory layer for Open WebUI-oriented workflows. It is designed to answer a specific problem: long-running work with LLMs loses continuity across sessions, projects, imported histories, preferences, decisions, corrections, and style constraints.

The system does not treat chat logs as memory directly. It treats raw messages as evidence, analyzes them into sentence-level communication units, routes those units into memory-candidate pipelines, consolidates evidence-backed claims, and retrieves only bounded, scoped, and privacy-aware context when needed.

The core sequence is:

```text
Open WebUI chat
-> Memorist Filter inlet/outlet
-> raw evidence capture
-> sentence units
-> Jakobson communication analysis
-> memory signal routing
-> specialized candidate extraction
-> trust/privacy/injection review
-> consolidation and memory versions
-> projections: FTS, active blocks, embeddings, graph preview
-> preflight retrieval planning
-> bounded Memory Context Attachment
-> main chat model receives separate memory context
```

The user prompt remains unchanged. Memory is attached as separate, untrusted, provenance-aware context.

## What this project is not

Memorist is not an official Open WebUI release. It is not a replacement for Open WebUI. It does not own the main chat UI. It does not claim production-ready Full Mode. It does not claim that prompt injection can be eliminated. It does not send telemetry by default. It does not require a cloud service in Lite Mode.

## Core guarantees

- Local-first by default.
- `MEMORIST_LOCAL_ONLY=false` is rejected.
- SQLite is the Lite ledger.
- PostgreSQL is the Full ledger.
- FalkorDB is a rebuildable graph projection, not the source of truth.
- Main chat model selection remains owned by Open WebUI.
- Memorist only observes the main chat model as `main_chat_observed`.
- Preflight, memory extraction, embedding, import reconstruction, block compaction, and privacy sensitivity are separate Memorist model roles.
- Remote/non-local model profiles require explicit privacy acknowledgement before becoming role defaults.
- Raw API keys, tokens, passwords, and credentials are rejected from persisted model profile data.
- Memory Worker prompts are versioned, schema-bound, output-validated, and audit-linked.
- Retrieved/imported memory is treated as untrusted data.
- Official ChatGPT/OpenAI export ZIPs and extracted `conversations.json` files can be
  imported with durable, resumable full-memory reconstruction for every eligible message;
  see `docs/import.md`.
- Chat fails open when Memorist is unavailable, unless explicitly configured otherwise.
- Forget workflows include preview, confirmation, execution, receipt, and residue checks.
- Generated packages and runtime artifacts are not committed by default.

## Runtime modes

| Mode | Canonical store | Graph | Scheduler | Current claim |
|---|---|---|---|---|
| Lite | SQLite | Disabled by default | SQLite write actor | Beta-candidate local path |
| Full | PostgreSQL | FalkorDB projection | Hot scheduler + durable PostgreSQL jobs/outbox | Experimental preview |
| Full degraded | PostgreSQL | FalkorDB down/unavailable | Hot scheduler + durable PostgreSQL jobs/outbox | Explicit degraded mode only |

Full Mode certification requires external evidence. Unit tests and mock tests are not enough to upgrade Full Mode to beta-supported.

## Repository layout

```text
.
├── memorist-core/                # FastAPI/Pydantic/uv core service
│   ├── migrations/               # SQLite Lite migrations
│   ├── migrations_postgres/      # PostgreSQL Full migrations
│   ├── src/memcore/              # Core runtime packages
│   └── tests/                    # Core tests
├── open-webui-integration/       # Filter, Function, shared client, fixtures, contract tests
├── docs/                         # Architecture, install, runtime, privacy, import, Full Mode docs
├── release/                      # Smoke tests, scanners, manifests, release documents
├── installer/scripts/            # Release assembly helpers
├── scripts/                      # Baseline, cleanup, source-tree, Full Mode checks
├── docker-compose.lite.yml       # Local Lite compose
├── docker-compose.full.yml       # Experimental Full compose
├── .env.example                  # Safe example configuration
├── Makefile                      # Developer/release commands
├── GITHUB_BASELINE.md            # Current baseline status
├── HANDOFF.md                    # Maintainer handoff
├── KNOWN_LIMITATIONS.md          # Current limitations
├── RELEASE_NOTES.md              # Development baseline notes
└── RELEASE_DECISION.md           # Release posture
```

## Requirements

- Python `>=3.12`
- `uv`
- Docker, optional for Lite and required for real Full/compose certification
- Open WebUI, if testing the integration in an actual Open WebUI instance

The project is tested through `uv`. Bare system Python versions below 3.12 are not supported.

## Quick start: Windows one-click (non-developer)

For end users who just want to run Memorist locally — **no Git, Python, or `uv`
needed**, only Docker Desktop:

1. Download and unzip the release package (`memorist-openwebui-<version>.zip`).
2. Double-click **`Memorist.cmd`** (or run `.\Install-Memorist.ps1`).
3. Follow the short wizard: it checks Docker, generates a private `.env`,
   optionally captures a provider API key locally, starts the services, and
   opens <http://localhost:3000>.

Lifecycle scripts ship in the package: `Start-Memorist.ps1`, `Stop-Memorist.ps1`,
`Restart-Memorist.ps1`, `Show-Memorist-Logs.ps1`, `Reset-Memorist-Data.ps1`,
`Uninstall-Memorist.ps1`. The same scripts run under PowerShell 7 on macOS/Linux.
Provider keys are written only to the local, git-ignored `.env` and referenced by
**name** in the Memory Setup UI — see [`docs/windows-local-install.md`](docs/windows-local-install.md)
and [`docs/local-release.md`](docs/local-release.md).

## Quick start: Lite Mode

Lite Mode is the supported local development path.

```bash
git clone https://github.com/YOUR_USERNAME/open-webui-memorist-edition.git
cd open-webui-memorist-edition
cp .env.example .env
docker compose -f docker-compose.lite.yml up --build
```

Check the service:

```bash
curl http://localhost:8777/health
curl http://localhost:8777/memcore/diagnostics/daily
```

Local development without Docker:

```bash
cd memorist-core
python -m uv sync --all-extras --dev
python -m uv run uvicorn memcore.main:app --host 0.0.0.0 --port 8777 --reload
```

## Open WebUI integration

Memorist integrates through a server-side Open WebUI Filter and an optional status Function under `open-webui-integration/`.

The current automated evidence is contract/fixture based:

```bash
cd memorist-core
python -m uv run pytest ../open-webui-integration/memorist/tests -q
```

The pinned real container smoke is manual/pending unless explicitly run:

```bash
make openwebui-container-smoke
```

Do not claim broad Open WebUI version-matrix compatibility until real container smoke and version-matrix tests are added.

## Memory Intelligence Core

The current memory pipeline is sentence-first and evidence-grounded.

Core principle:

```text
Raw message -> sentence unit -> Jakobson annotation -> memory signal route
-> memory candidate -> consolidated memory version -> retrieval projection
```

The sentence-level Jakobson layer is the primary semantic lens. It identifies:

- sender/addresser
- receiver/addressee
- message
- context/referent
- code/register
- contact/channel
- dominant and secondary communication functions

This prevents the system from treating every sentence as a generic fact. A team instruction, a user preference, a terminology rule, a project process description, and an emotional stance are routed differently.

The legacy `memorist.unit_analysis` prompt is retained only for aggregate/compatibility behavior. It is not the primary semantic memory pipeline.

See:

- `docs/memory-intelligence-core.md`
- `docs/concept-glossary.md`
- `docs/prompt-pack.md`
- `docs/memory-worker-prompts.md`

## Model Control Plane

Memorist separates model roles instead of using the main chat model for everything.

| Role | Purpose |
|---|---|
| `main_chat_observed` | Metadata only; selected and owned by Open WebUI |
| `preflight` | Bounded retrieval/attachment planning before main chat |
| `memory_extraction` | Asynchronous post-response memory extraction |
| `embedding` | Optional semantic indexing |
| `import_reconstruction` | Optional imported-history reconstruction |
| `block_compaction` | Active memory block compaction |
| `privacy_sensitivity` | Sensitive-memory classification |

Safe Lite defaults are deterministic/local-first. Non-local providers require privacy acknowledgement before activation as role defaults.

See:

- `docs/model-control-plane.md`
- `docs/model-privacy.md`
- `docs/model-costs.md`

## Memory Worker Prompt Pack v2

Prompt Pack v2 is implemented as the current non-chat prompt contract baseline.

It includes versioned, schema-bound prompts for:

- preflight planning
- Jakobson sentence analysis
- memory signal routing assist
- route-specific extraction
- memory consolidation assist
- contradiction detection
- block compaction
- import reconstruction
- privacy sensitivity

Prompt executions are audit-linked by prompt ID, version, role, model profile, input hash, output hash, validation status, and generated artifacts where applicable.

See:

- `docs/prompt-pack.md`
- `docs/prompt-safety.md`
- `docs/memory-worker-prompts.md`

## Full Mode preview

Full Mode is implemented as an experimental preview. It is not beta-supported yet.

Full Mode means:

```text
PostgreSQL canonical store
+ PostgreSQL durable jobs/outbox
+ hot scheduler
+ FalkorDB graph projection
+ graph-aware retrieval preview
+ graph-aware forget/residue certification scripts
+ SQLite-to-PostgreSQL migration tooling
+ docker-compose.full.yml
```

Run the Full checker:

```bash
python scripts/full_mode_check.py
```

If Docker is unavailable and no test DSNs are provided, Full external gates will skip. That is expected, and it blocks Full beta support.

For external certification, use a Docker-capable environment or provide test services:

```bash
export MEMORIST_TEST_POSTGRES_DSN="postgresql://..."
export MEMORIST_TEST_FALKORDB_URL="redis://..."
python scripts/full_mode_check.py
```

See:

- `docs/full-mode.md`
- `docs/postgres.md`
- `docs/falkordb.md`
- `docs/hot-scheduler.md`
- `docs/sqlite-to-postgres.md`
- `docs/storage-profiles.md`

## Test and audit commands

Baseline quality gate:

```bash
python scripts/baseline_check.py
```

Core gate:

```bash
make check
```

If `make` is unavailable, run:

```bash
cd memorist-core
python -m uv sync --all-extras --dev
python -m uv run ruff check .
python -m uv run mypy src/memcore
python -m uv run pytest -q
```

Focused gates:

```bash
make model-control-tests
make memory-worker-prompt-pack-test
make openwebui-contract-tests
make smoke-daily
make smoke-import-heavy-ci
make heritage-roundtrip
make forget-residue
make consistency-check
make recovery-tests
```

Full Mode gates:

```bash
python scripts/full_mode_check.py
```

Security and cleanup:

```bash
python scripts/clean_artifacts.py --check
python scripts/clean_artifacts.py --apply
python scripts/scan_source_tree.py
```

## Package and release workflow

Generated packages should be rebuilt from a clean committed tree before publishing.

Source package:

```bash
python release/source_package.py --out release/source/open-webui-memorist-edition-source.zip
python -m release.scan_source_tree release/source/open-webui-memorist-edition-source.zip
```

RC package:

```bash
python installer/scripts/assemble_rc.py
python -m release.scan_forbidden_files release/rc/memorist-openwebui-0.2.0-beta.1.zip
cd memorist-core
python -m uv run python ../release/tests/rc_package_schema.py
python -m uv run python ../release/tests/version_consistency.py
```

Do not commit generated package archives by default:

```text
release/source/*.zip
release/source/*.sha256
release/rc/*.zip
release/rc/*.sha256
```

Attach them as GitHub Release assets only after rebuilding them from a clean, committed tree.

## Configuration

Start from:

```bash
cp .env.example .env
```

Important defaults:

```env
MEMORIST_LOCAL_ONLY=true
MEMORIST_RUNTIME_PROFILE=lite
MEMORIST_CANONICAL_STORE=sqlite
MEMORIST_DB_PATH=./data/memorist.sqlite
MEMORIST_OBJECT_STORE_PATH=./data/objects
MEMORIST_GRAPH_BACKEND=disabled
MEMORIST_PREFLIGHT_ENABLED=true
MEMORIST_PREFLIGHT_FAIL_OPEN=true
```

Full Mode requires PostgreSQL:

```env
MEMORIST_RUNTIME_PROFILE=full
MEMORIST_CANONICAL_STORE=postgres
MEMORIST_POSTGRES_DSN=postgresql://...
MEMORIST_GRAPH_BACKEND=falkordb
MEMORIST_FALKORDB_URL=redis://...
```

Full Mode must not silently fall back to SQLite canonical storage.

## Security and privacy

Memorist is designed for local-first operation, but local-first does not mean risk-free.

Current security posture:

- `.env` files with secrets are ignored and must not be committed.
- Raw provider secrets are rejected from persisted model profile storage.
- Remote model roles require explicit privacy acknowledgement before use as defaults.
- Retrieved/imported content is treated as untrusted data.
- Prompt injection is tested but cannot be eliminated.
- Forget receipts avoid raw erased content.
- Residue checks cover canonical memory, evidence, active blocks, attachments, FTS, import payloads, and graph layers where enabled.
- Release/source packages are scanned for forbidden files and secret-like artifacts.

See:

- `docs/security.md`
- `docs/model-privacy.md`
- `docs/forget-residue.md`
- `KNOWN_LIMITATIONS.md`

## Development workflow

Recommended pre-push sequence:

```bash
python scripts/clean_artifacts.py --check
python scripts/baseline_check.py
git status --short
```

Recommended pre-release sequence:

```bash
python scripts/clean_artifacts.py --apply
python scripts/clean_artifacts.py --check
python scripts/baseline_check.py
python release/source_package.py --out release/source/open-webui-memorist-edition-source.zip
python installer/scripts/assemble_rc.py
```

Create an initial baseline commit:

```bash
git add .
git status --short
git commit -m "chore: establish v0.2.0-beta.1 development baseline"
```

Before committing, verify that generated ZIPs, checksums, virtual environments, caches, runtime databases, logs, and `.env` files are not staged.

## Documentation map

| Topic | Document |
|---|---|
| Current GitHub status | `GITHUB_BASELINE.md` |
| Handoff | `HANDOFF.md` |
| Known limitations | `KNOWN_LIMITATIONS.md` |
| Architecture | `docs/architecture.md` |
| Memory intelligence | `docs/memory-intelligence-core.md` |
| Prompt Pack v2 | `docs/prompt-pack.md` |
| Model Control Plane | `docs/model-control-plane.md` |
| Lite install | `docs/install-lite.md` |
| Full install | `docs/install-full.md` |
| Full Mode | `docs/full-mode.md` |
| Memory-control contract | `docs/memory-control-contract.md` |
| Open WebUI compatibility | `docs/openwebui-compatibility.md` |
| Import | `docs/import.md` |
| Heritage | `docs/heritage-roundtrip.md` |
| Forget/residue | `docs/forget-residue.md` |
| Troubleshooting | `docs/troubleshooting.md` |

Historical design documents live under `docs/historical/` and do not describe the current implementation contract.

## Roadmap

Near-term priorities:

1. Run Full Mode certification on a Docker-capable or DSN-configured host.
2. Add automated pinned Open WebUI container smoke.
3. Expand semantic evaluation for Jakobson routing and Prompt Pack v2.
4. Improve operator UI/UX for Model Control profiles, privacy acknowledgement, and role defaults.
5. Add broader Open WebUI version-matrix tests.
6. Continue hardening import, Heritage, forget, and graph-residue workflows.

## Release posture

Use this repository as a development baseline, not as a stable public release.

Correct current wording:

```text
Lite Mode: beta-candidate
Full Mode: experimental preview, materially improved
Open WebUI integration: contract-tested; pinned container smoke pending/manual
```

Incorrect current wording:

```text
Public Beta GO
Full Mode beta support approved
Open WebUI production-certified
Stable release
```

## License

MIT. See `LICENSE`.

## Attribution

Open WebUI remains the parent chat product and integration target. Memorist is a local companion memory runtime and should not be presented as an official Open WebUI release unless that status is explicitly obtained.
