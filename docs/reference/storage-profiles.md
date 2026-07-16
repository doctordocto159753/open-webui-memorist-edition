# Storage Profiles

Memorist has explicit runtime profiles:

```text
SQLite is the Lite ledger.
PostgreSQL is the Full ledger.
FalkorDB is the graph memory map.
```

## Lite

Lite uses SQLite as the canonical store. It remains the recommended local daily
profile and has no PostgreSQL or FalkorDB requirement.

```env
MEMORIST_RUNTIME_PROFILE=lite
MEMORIST_CANONICAL_STORE=sqlite
MEMORIST_GRAPH_BACKEND=disabled
MEMORIST_HOT_SCHEDULER=disabled
```

SQLite Lite includes WAL, foreign keys, bounded retry, local object storage,
local FTS, and the SQLite write actor for hot Open WebUI writes, import commit,
Heritage restore, and privacy mutation paths.

## Full

Full uses PostgreSQL as the canonical ledger. It adds durable PostgreSQL
jobs/outboxes, Hot Scheduler runnable references, FalkorDB graph projection, and
SQLite-to-PostgreSQL migration tooling.

```env
MEMORIST_RUNTIME_PROFILE=full
MEMORIST_CANONICAL_STORE=postgres
MEMORIST_POSTGRES_DSN=postgresql://memorist:memorist@postgres:5432/memorist
MEMORIST_GRAPH_BACKEND=falkordb
MEMORIST_FALKORDB_URL=redis://falkordb:6379
MEMORIST_HOT_SCHEDULER=in_memory
```

Startup policy:

- `runtime_profile=full` refuses `canonical_store=sqlite`.
- `canonical_store=postgres` requires `MEMORIST_POSTGRES_DSN`.
- Full checks PostgreSQL health before readiness.
- Full requires `graph_backend=falkordb` unless explicit degraded mode is set.
- The hot scheduler stores only runnable references; durable payloads stay in
  PostgreSQL.

Current status:

```text
Full Mode: experimental preview; external certification incomplete.
```

Run `python scripts/full_mode_check.py` to generate the local certification
report before making stronger Full Mode claims.
