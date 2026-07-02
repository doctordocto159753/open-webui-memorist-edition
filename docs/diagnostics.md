# Diagnostics

Diagnostics are local-only and do not send telemetry.

## Endpoints

```sh
curl http://localhost:8777/memcore/diagnostics/write-actor
curl http://localhost:8777/memcore/diagnostics/daily
curl http://localhost:8777/memcore/openwebui/status
```

## Daily Diagnostic Fields

`/memcore/diagnostics/daily` returns:

- `mode`: active retrieval mode;
- `health.sqlite`: whether the SQLite file exists;
- `health.write_actor`: `ok` when the writer has started, `idle` before first hot write;
- `health.graph_backend`: configured graph backend;
- `preflight.last_status`: latest preflight event type if present;
- `queues.write_depth`: writer queue depth;
- `queues.jobs_pending`: pending background jobs;
- `queues.import_paused`: whether any import is paused;
- `storage.sqlite_size_mb`: approximate SQLite file size;
- `storage.object_store_size_mb`: approximate object store size;
- `warnings`: local warnings such as `write_queue_depth_high`.

## Triage

If chat works but memory is absent:

1. check `GET /memcore/health`;
2. check Open WebUI Filter valves;
3. check `/memcore/openwebui/status`;
4. check `/memcore/diagnostics/daily`;
5. verify `MEMORIST_PREFLIGHT_ENABLED=true`;
6. verify the model has enough context budget.

If imports slow normal chat:

1. pause active imports;
2. inspect `queues.write_depth`;
3. lower `MEMORIST_IMPORT_BATCH_SIZE`;
4. lower `MEMORIST_IMPORT_MAX_WRITE_QUEUE_DEPTH`;
5. keep `MEMORIST_IMPORT_LOW_PRIORITY=true`.
