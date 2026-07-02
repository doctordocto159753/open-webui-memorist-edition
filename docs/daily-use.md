# Daily Use

Memorist is designed to stay out of the user’s way during normal Open WebUI use. The chat UI should keep working even when memory retrieval, import, or diagnostics degrade.

## Normal Runtime Flow

```text
Open WebUI inlet
-> parse payload
-> resolve Memorist session
-> capture user message through SQLite writer
-> run preflight
-> inject bounded memory context if available
-> Open WebUI model response
-> capture assistant output through SQLite writer
```

The Filter is fail-open by default. If Memorist Core is down or preflight times out, Open WebUI receives the original chat payload.

## Daily Checks

Run from the repository root:

```sh
make smoke-daily
```

The smoke starts a temporary local FastAPI app and verifies:

- health endpoint;
- workspace/session/message base APIs;
- Open WebUI session resolution and idempotent capture;
- adaptive attachment budget;
- write actor and daily diagnostics.

## Daily API Surfaces

- `GET /memcore/health`
- `GET /memcore/version`
- `GET /memcore/diagnostics/daily`
- `GET /memcore/diagnostics/write-actor`
- `POST /memcore/openwebui/session/resolve`
- `POST /memcore/openwebui/messages/capture`
- `POST /memcore/preflight`

## Safe Defaults

- `MEMORIST_LOCAL_ONLY=true`
- `MEMORIST_GRAPH_BACKEND=disabled`
- `MEMORIST_FAIL_OPEN=true`
- `MEMORIST_IMPORT_LOW_PRIORITY=true`
- `MEMORIST_IMPORT_MAX_WRITE_QUEUE_DEPTH=500`

If daily chat feels slow, check `/memcore/diagnostics/daily` first, then pause any active imports.
