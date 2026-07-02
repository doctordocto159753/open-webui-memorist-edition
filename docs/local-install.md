# Local install

## Requirements

- Python 3.12
- uv
- Docker with Compose support, for container execution

## Python service

```sh
make install
make dev
```

Health check:

```sh
curl http://localhost:8777/memcore/health
```

Effective local configuration:

```sh
curl http://localhost:8777/memcore/config/effective
```

Preflight retrieval uses persisted session/message UUIDs:

```sh
curl -X POST http://localhost:8777/memcore/preflight \
  -H "Content-Type: application/json" \
  -d '{"session_uuid":"...","input_message_uuid":"...","retrieval_mode":"standard","token_budget":1800}'
```

Privacy requests use preview/confirm/execute:

```sh
curl -X POST http://localhost:8777/memcore/privacy/requests/preview \
  -H "Content-Type: application/json" \
  -d '{"request_type":"forget_memory","target_type":"memory","target_uuid":"...","requested_scope":{"memory_uuid":"..."},"actor_type":"user"}'
```

Import inspection and Heritage verification are local CLI operations:

```sh
cd memorist-core
uv run python -m memcore.imports inspect path/to/export.zip
uv run python -m memcore.heritage verify path/to/heritage.zip
uv run python -m memcore.heritage restore path/to/heritage.zip --db-path ./data/restored.sqlite --dry-run
```

## Docker lite

```sh
make dev-up-lite
```

## Docker full

```sh
make dev-up-full
```

The full compose file keeps `MEMORIST_GRAPH_BACKEND=disabled` for the core service. FalkorDB is present as a placeholder profile for future graph-backed phases.
