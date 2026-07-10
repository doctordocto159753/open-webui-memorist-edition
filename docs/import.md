# Import Operations

Import is explicit and non-destructive. Official ChatGPT/OpenAI exports are supported as
either the original ZIP archive or an extracted `conversations.json` file. Existing
Open WebUI, Claude, Gemini, generic Memorist, and manual transcript adapters remain
available.

```text
upload/stage
-> inspect
-> reconstruct
-> dry-run
-> review report
-> commit
-> process full reconstruction batches
-> review progress/report and retry failures
```

## Supported ChatGPT/OpenAI Inputs

- Official export ZIP with `conversations.json` at the root or below an export folder.
- Standalone extracted `conversations.json`.
- `.jsonl` remains accepted by the generic staging path when an adapter recognizes it.

The mapping tree is retained, including source conversation/message IDs, roots,
parent/child relationships, `current_node`, active/alternate branches, timestamps,
model metadata, and attachment metadata. Null nodes and missing content are safe.
Provider-internal reasoning-like parts are quarantined and are not exposed as visible
message text. Attachment metadata does not imply that unavailable binary payloads exist.

## Safety Model

- ZIP traversal, absolute paths, links/devices, excessive file counts, expansion size,
  and compression-ratio violations are rejected before extraction.
- Standalone JSON is copied to a generated object-store name and is subject to the
  expanded-size limit.
- Malformed or unrecognized JSON produces an actionable inspection issue.
- Imported text is untrusted data. It is never promoted to system instructions.
- Provider errors returned by reconstruction APIs are sanitized; API keys are never
  persisted in import state or reports.
- Use synthetic fixtures only. No real user exports belong in the repository.

## Dry-run

Use `processing_mode` in the dry-run request to preview the intended commit:

```json
{"processing_mode":"full_memory_reconstruction"}
```

The report includes format/platform, conversation and message totals, eligibility and
grouped skip reasons, duplicates, expected sessions/messages/mappings/jobs, processing
priority, graph projection state, and the resolved `memory_extraction` role/profile.
It also reports whether deterministic fallback will be used, whether a configured
profile is enabled and privacy-acknowledged, and whether its secret environment variable
is available. Full reconstruction may consume substantial time and tokens. It does not
sample or skip messages for cost reasons.

Dry-run does not create canonical sessions, messages, or memory artifacts.

## Processing Modes

- `none`: create canonical sessions/messages and mappings only. No import memory job is
  scheduled.
- `extract_candidates`: retain the legacy low-priority text-unitization scheduling path.
- `full_memory_reconstruction`: schedule the canonical `memory_extraction` pipeline for
  every eligible imported user and assistant message.

Messages with non-empty visible text and role `user` or `assistant` are eligible,
including assistant messages and alternate branches. System/tool/developer/unknown roles,
null mapping nodes, empty visible text, reasoning-only content, and binary-only attachment
nodes are skipped with an explicit reason.

Each message receives durable state in `import_message_processing_status`: `queued`,
`running`, `succeeded`, `failed`, `skipped`, or `already_processed`. State includes source
and target IDs, job/run/profile identifiers, retry count, sanitized error, input hash, and
timestamps. The unique import/source/mode identity and canonical memory-worker keys make
resume and retry idempotent.

Full reconstruction reuses Model Control's `memory_extraction` default. Disabled profiles,
remote profiles without privacy acknowledgement, and profiles whose required secret env
var is unavailable are not selected for imported work; deterministic fallback is used and
reported. Prompt executions and model usage events carry the import run and job IDs.

## API

The existing flow remains compatible:

- `POST /memcore/imports/upload`
- `POST /memcore/imports/{import_run_uuid}/inspect`
- `POST /memcore/imports/{import_run_uuid}/reconstruct`
- `POST /memcore/imports/{import_run_uuid}/dry-run`
- `GET /memcore/imports/{import_run_uuid}/dry-run-report`
- `POST /memcore/imports/{import_run_uuid}/commit`
- `GET /memcore/imports/{import_run_uuid}/progress`
- `POST /memcore/imports/{import_run_uuid}/pause`
- `POST /memcore/imports/{import_run_uuid}/resume`
- `POST /memcore/imports/{import_run_uuid}/cancel`

Full reconstruction adds:

- `POST /memcore/imports/{import_run_uuid}/process` with a bounded `batch_size`.
- `POST /memcore/imports/{import_run_uuid}/retry-failed`.
- `GET /memcore/imports/{import_run_uuid}/processing-report`.
- `GET /memcore/imports/{import_run_uuid}/messages/processing-status`.

Commit only schedules durable low-priority jobs; it does not create an unbounded request.
Call the bounded process endpoint from the import worker/UI until progress is terminal.
An import using full reconstruction is `processing` after commit and becomes
`fully_reconstructed` only when every message has a terminal state. Failed messages are
terminal for reporting but can be re-queued without re-importing the archive.

Live capture remains higher priority than import work. Pause/cancel prevents future import
batches; completed canonical writes are not rolled back.

## CLI

```sh
cd memorist-core
uv run python -m memcore.imports inspect path/to/export.zip
uv run python -m memcore.imports inspect path/to/conversations.json --dry-run \
  --processing-mode full_memory_reconstruction
uv run python -m memcore.imports inspect path/to/conversations.json --commit \
  --processing-mode full_memory_reconstruction
```

CLI commit stages and schedules reconstruction. Use the API worker endpoints for bounded
processing, progress, and retry.

## UI status

This repository does not currently ship a complete import UI. The API and CLI are the
truthful primary interfaces for this release; a future UI should expose inspect,
reconstruct, dry-run approval, processing-mode selection, progress, reports, and failed
message retry without changing these contracts.

## Storage-mode Note

The current import orchestration routes use the SQLite control store in both product
profiles. This change adds matching PostgreSQL schema for per-message reconstruction state,
but does not silently redirect existing import APIs to the Full/PostgreSQL canonical store.
Direct Full/PostgreSQL import orchestration parity remains a follow-up; the limitation is
explicit so Full Mode is not presented as complete where it is not.
