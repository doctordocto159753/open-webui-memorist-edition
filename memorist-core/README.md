# memorist-core

FastAPI service foundation for the local-first Memorist runtime. The current supported baseline is SQLite Lite with sentence-level Jakobson memory intelligence, Model Control Plane backend/runtime integration, and Memory Worker Prompt Pack v2 implemented as development baselines. PostgreSQL canonical Full Mode foundations, FalkorDB projection scaffolding, durable PostgreSQL job/outbox DDL, hot scheduler lanes, and SQLite-to-PostgreSQL migration tooling remain experimental preview paths until external Full gates pass.

## Run locally

```sh
uv sync --dev
uv run uvicorn memcore.main:app --host 0.0.0.0 --port 8777 --reload
```

## Endpoints

- `GET /memcore/health`
- `GET /memcore/version`
- `GET /memcore/config/effective`
- `POST /memcore/workspaces`
- `GET /memcore/workspaces`
- `POST /memcore/projects`
- `GET /memcore/projects`
- `POST /memcore/sessions`
- `GET /memcore/sessions`
- `POST /memcore/messages`
- `GET /memcore/messages/{message_uuid}/lineage`
- `POST /memcore/budget/attachment`
- `GET /memcore/model-registry`
- `POST /memcore/model-registry`
- `PATCH /memcore/model-registry/{model_profile_uuid}`
- `GET /memcore/model-control/roles`
- `GET /memcore/model-control/profiles`
- `POST /memcore/model-control/profiles`
- `PATCH /memcore/model-control/profiles/{model_profile_uuid}`
- `POST /memcore/model-control/profiles/{model_profile_uuid}/test`
- `GET /memcore/model-control/defaults`
- `POST /memcore/model-control/defaults`
- `GET /memcore/model-control/usage`
- `GET /memcore/model-control/privacy`
- `GET /memcore/model-control/health`
- `POST /memcore/model-control/estimate-cost`
- `GET /memcore/diagnostics/write-actor`
- `GET /memcore/diagnostics/daily`
- `GET /memcore/scheduler/status`
- `GET /memcore/graph/status`
- `GET /memcore/graph/diagnostics`
- `POST /memcore/graph/project-pending`
- `POST /memcore/graph/rebuild`
- `POST /memcore/retrieval/plan`
- `POST /memcore/retrieval/run`
- `GET /memcore/retrieval/runs/{retrieval_run_uuid}`
- `GET /memcore/retrieval/runs/{retrieval_run_uuid}/candidates`
- `POST /memcore/attachments/build`
- `GET /memcore/attachments/{attachment_uuid}`
- `GET /memcore/attachments/{attachment_uuid}/sources`
- `POST /memcore/preflight`
- `POST /memcore/assistant-response/completed`
- `POST /memcore/blocks/{block_uuid}/build`
- `GET /memcore/blocks/{block_uuid}/versions`
- `POST /memcore/blocks/{block_uuid}/compact`
- `GET /memcore/responses/{message_uuid}/memory-trace`
- `POST /memcore/memory-feedback`
- `GET /memcore/memories/{memory_uuid}/inspect`
- `POST /memcore/memories/{memory_uuid}/change-requests`
- `POST /memcore/privacy/requests/preview`
- `POST /memcore/privacy/requests/{request_uuid}/confirm`
- `POST /memcore/privacy/requests/{request_uuid}/execute`
- `POST /memcore/privacy/forget/preview`
- `POST /memcore/privacy/forget/{request_uuid}/confirm`
- `POST /memcore/privacy/forget/{request_uuid}/execute`
- `GET /memcore/privacy/forget/{request_uuid}/residue`
- `POST /memcore/imports/upload`
- `POST /memcore/imports/{import_run_uuid}/inspect`
- `POST /memcore/imports/{import_run_uuid}/reconstruct`
- `POST /memcore/imports/{import_run_uuid}/dry-run`
- `POST /memcore/imports/{import_run_uuid}/commit`
- `GET /memcore/imports/{import_run_uuid}/progress`
- `POST /memcore/imports/{import_run_uuid}/pause`
- `POST /memcore/imports/{import_run_uuid}/resume`
- `POST /memcore/imports/{import_run_uuid}/cancel`
- `POST /memcore/heritage/export`
- `GET /memcore/heritage/verify`
- `GET /memcore/heritage/inspect`
- `POST /memcore/heritage/restore`
- `POST /memcore/openwebui/session/resolve`
- `POST /memcore/openwebui/messages/capture`
- `GET /memcore/openwebui/status`

The effective configuration endpoint returns non-secret runtime settings and feature flags. Secret-like keys are redacted by policy.

## Phase 3 retrieval

Phase 3 provides the local pre-send path: deterministic planning, local hybrid candidate generation, explainable ranking, bounded Memory Context Attachments, preflight failure isolation, and assistant-response linking.

## Phase 4 governance

Phase 4 provides versioned Active Memory Blocks, delivery/attribution traces, user feedback, correction/undo requests, and dependency-aware privacy erasure receipts.

## Phase 5 import and heritage

Phase 5 provides secure local ZIP staging, provider adapter probing, conversation reconstruction, dry-run dedupe reports, explicit commit into canonical sessions/messages, and offline-verifiable Heritage export/verify/restore tools.

```sh
uv run python -m memcore.imports inspect path/to/export.zip
uv run python -m memcore.heritage verify path/to/heritage.zip
uv run python -m memcore.heritage restore path/to/heritage.zip --db-path ./data/restored.sqlite --dry-run
```

## Phase 6 hardening

Phase 6 provides offline evaluation fixtures, adversarial prompt-injection checks, Lite/Standard/Full runtime budgets, performance smoke reports, consistency checks, safe SQLite backup, and storage maintenance commands.

```sh
uv run python -m memcore.eval run --dataset src/memcore/eval/fixtures/basic.ijsonl
uv run python -m memcore.performance perf-smoke --profile lite
uv run python -m memcore.reliability check
uv run python -m memcore.reliability backup --out backup.sqlite
uv run python -m memcore.reliability secure-delete-check
```

## Phase 7 Open WebUI integration

Phase 7 adds local Open WebUI session resolution, idempotent message capture, sanitized integration status, and a fail-open Filter/Function bundle under `../open-webui-integration/memorist`.

## Memory Worker prompt pack

The worker prompt pack lives in `src/memcore/memory_worker/prompts/`. Prompt Pack v2 is implemented as the current schema-bound prompt contract baseline. It registers versioned system prompts for sentence-level Jakobson analysis, legacy aggregate unit analysis, candidate extraction, consolidation assistance, preflight planning, block compaction, import reconstruction, contradiction detection, and privacy sensitivity. `memorist.jakobson_sentence_analysis` is the primary semantic prompt for the Memory Intelligence Core / Jakobson Pipeline. `memorist.unit_analysis` is retained only as aggregate/legacy compatibility.

## Daily-use hardening

Hot Open WebUI session/capture writes are serialized through a local SQLite write actor with idempotency replay and diagnostics. Import progress supports pause/resume/cancel and backpressure-aware bounded commit. Adaptive attachment budgets can use a local model registry for custom model names.

## Model Control Plane

The Model Control Plane is implemented as a backend/runtime baseline. It separates `main_chat_observed`, `preflight`, `memory_extraction`, `privacy_sensitivity`, `block_compaction`, `import_reconstruction`, and `embedding` roles. Open WebUI keeps control of the main chat model; Memorist controls only local memory roles, records usage events, enforces explicit privacy acknowledgement for non-local profiles, and marks embedding records stale when the embedding default changes. UI polish and broader provider orchestration remain future hardening work.

## Heavy-import readiness

Import commit, Heritage restore, and privacy request mutation paths can run through the priority SQLite writer actor. P2 adds synthetic heavy Open WebUI fixtures, actor-batched import commit, Heritage export/verify/restore/compare, forget residue checks, consistency reports, and recovery smoke tests.

```sh
uv run python -m memcore.imports generate-heavy ../data/openwebui-heavy.zip --conversations 1000 --messages 2
uv run python -m memcore.reliability.consistency check --db-path ./data/memorist.sqlite --json-output ./data/reports/consistency.ijson
uv run python -m memcore.reliability recover --db-path ./data/memorist.sqlite
```

## Full Mode preview

Full Mode uses PostgreSQL as the canonical ledger and FalkorDB as a rebuildable graph projection:

```sh
uv run python -m memcore.storage.postgres parity-report
uv run python -m memcore.migrate sqlite-to-postgres --sqlite ./data/memorist.sqlite --postgres "$MEMORIST_POSTGRES_DSN" --dry-run
uv run python -m memcore.graph rebuild --store postgres
uv run python -m memcore.doctor
```

Full Mode must be configured with `MEMORIST_RUNTIME_PROFILE=full`, `MEMORIST_CANONICAL_STORE=postgres`, `MEMORIST_GRAPH_BACKEND=falkordb`, and `MEMORIST_HOT_SCHEDULER=in_memory`. It is experimental until PostgreSQL and FalkorDB smoke tests are run in the target environment.

## I-JSON validator

```sh
uv run python -m memcore.validators.ijson path/to/file.json
```

## Development checks

```sh
uv run pytest
uv run pytest tests/test_memory_worker_prompt_pack.py -q
uv run ruff check .
uv run mypy src tests
```
