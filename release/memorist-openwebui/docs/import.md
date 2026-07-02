# Import

Import is staged and explicit:

```text
upload -> inspect -> reconstruct -> dry-run -> commit
```

Provider schemas can change; unknown fields are preserved during staging and imported text is treated as untrusted data.

Daily-use controls:

- `GET /memcore/imports/{import_run_uuid}/progress`
- `POST /memcore/imports/{import_run_uuid}/pause`
- `POST /memcore/imports/{import_run_uuid}/resume`
- `POST /memcore/imports/{import_run_uuid}/cancel`

Recommended defaults:

```env
MEMORIST_IMPORT_BATCH_SIZE=100
MEMORIST_IMPORT_MAX_WRITE_QUEUE_DEPTH=500
MEMORIST_IMPORT_LOW_PRIORITY=true
```

Pause large imports if `/memcore/diagnostics/daily` reports high write queue depth.
