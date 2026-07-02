# Import Operations

Import is explicit and non-destructive. Provider archives are staged, inspected, reconstructed, dry-run, and only then committed.

```text
upload
-> inspect
-> reconstruct
-> dry-run
-> review report
-> commit
```

## Safety Model

- Archives are staged under the local object store.
- Unsafe archive paths and suspicious payloads are rejected before reconstruction.
- Provider text is treated as untrusted data.
- Dry-run reports dedupe decisions and expected writes before commit.
- Commit writes canonical sessions/messages and import mappings only after explicit approval.

## Progress and Control

Import progress is stored in `import_progress` and exposed through:

- `GET /memcore/imports/{import_run_uuid}/progress`
- `POST /memcore/imports/{import_run_uuid}/pause`
- `POST /memcore/imports/{import_run_uuid}/resume`
- `POST /memcore/imports/{import_run_uuid}/cancel`

Progress fields include:

- `phase`
- `records_total`
- `records_done`
- `records_failed`
- `current_batch`
- `throttled`
- `throttle_reason`
- `paused`
- `cancelled`

## Backpressure

The commit loop pauses if write actor queue depth reaches `MEMORIST_IMPORT_MAX_WRITE_QUEUE_DEPTH`. Imported processing jobs use lower priority by default so live Open WebUI capture remains responsive.

Recommended settings:

```env
MEMORIST_IMPORT_BATCH_SIZE=100
MEMORIST_IMPORT_MAX_JOBS_PER_MINUTE=60
MEMORIST_IMPORT_MAX_WRITE_QUEUE_DEPTH=500
MEMORIST_IMPORT_LOW_PRIORITY=true
MEMORIST_IMPORT_RECONSTRUCTION_DEFAULT=off
```

## CLI

```sh
cd memorist-core
uv run python -m memcore.imports inspect path/to/export.zip
```

Use the API flow for full upload/reconstruct/dry-run/commit until the import CLI is expanded.
