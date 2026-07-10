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
-> automatic reconstruction worker drains durable jobs
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
priority, graph projection state, and the resolved processing role/profile.
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
`running`, `succeeded`, `failed`, `skipped`, `already_processed`, or `cancelled`.
State includes source and target IDs, job/run/profile identifiers, retry count,
sanitized error, input hash, pipeline version, prompt-bundle version, model role,
processing identity, lease fields, and timestamps. Mapping identity is not treated as
proof of processing. A mapped message becomes `already_processed` only when a matching
successful current memory-processing run exists for the same message, content hash,
pipeline, prompt bundle, and model profile.

Full reconstruction resolves Model Control in this order: `import_reconstruction`,
then `memory_extraction`, then deterministic fallback. Disabled profiles, remote
profiles without privacy acknowledgement, and profiles whose required secret env var is
unavailable are not selected for remote work; deterministic fallback is used and
reported. Prompt executions and model usage events carry the truthful role/profile/model
that was actually used, plus import run and job IDs.

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

Full reconstruction adds administrative/debug endpoints:

- `POST /memcore/imports/{import_run_uuid}/process` with a bounded `batch_size`.
- `POST /memcore/imports/{import_run_uuid}/retry-failed`.
- `GET /memcore/imports/{import_run_uuid}/processing-report`.
- `GET /memcore/imports/{import_run_uuid}/messages/processing-status`.

Commit only schedules durable low-priority jobs; it does not create an unbounded request.
The import reconstruction worker automatically claims queued work in bounded batches,
releases the claim transaction, performs provider work outside the SQLite write actor,
and persists results afterward. The manual process endpoint remains for bounded
administrative execution and debugging.

An import using full reconstruction is `processing` after commit. It becomes
`fully_reconstructed` only when all eligible work succeeded or was verifiably already
processed. If eligible messages fail permanently, the run becomes
`completed_with_failures`. Skipped messages are acceptable only when they carry explicit
ineligibility reasons. Retry re-queues failed eligible messages without recreating
canonical sessions/messages.

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

CLI commit stages and schedules reconstruction. The background worker drains queued
work when Memorist Core is running; use the API worker endpoints for bounded
administrative processing, progress, and retry.

## UI status

The Open WebUI integration registers a Memorist import workflow under the Memorist
settings area. It supports source path selection, upload/inspect/reconstruct/dry-run,
processing-mode selection, explicit full-reconstruction confirmation, progress counts,
pause, resume, cancel, retry failed, token counts when available, and sanitized report
display. The UI calls the same API contracts listed above.

## Storage-mode Note

Lite Mode uses SQLite for import runs, mappings, canonical sessions/messages, durable
per-message processing state, and memory artifacts. Full Mode import endpoints no longer
silently fall back to SQLite; they fail explicitly until PostgreSQL import repositories
and the PostgreSQL import worker are completed. Matching PostgreSQL migrations exist for
per-message reconstruction state, but runtime Full/PostgreSQL import parity is not
claimed by this document.

## Worker Configuration

```text
MEMORIST_IMPORT_RECONSTRUCTION_WORKER_ENABLED=true
MEMORIST_IMPORT_RECONSTRUCTION_CONCURRENCY=1
MEMORIST_IMPORT_RECONSTRUCTION_WORKER_BATCH_SIZE=5
MEMORIST_IMPORT_RECONSTRUCTION_LEASE_SECONDS=300
MEMORIST_IMPORT_RECONSTRUCTION_MAX_ATTEMPTS=5
MEMORIST_IMPORT_RECONSTRUCTION_RETRY_BASE_SECONDS=10
MEMORIST_IMPORT_RECONSTRUCTION_POLL_SECONDS=1.0
```
