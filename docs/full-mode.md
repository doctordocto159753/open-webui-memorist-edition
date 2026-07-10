# Full Mode

Full Mode is the local heavy-workload profile:

```text
SQLite is the Lite ledger.
PostgreSQL is the Full ledger.
FalkorDB is the graph memory map.
```

Current certification wording:

```text
Full Mode: experimental preview; external certification incomplete.
```

That wording must remain until every Full gate in `scripts/full_mode_check.py`
passes against real PostgreSQL, real FalkorDB, and `docker-compose.full.yml`.

## Architecture

```text
Open WebUI Filter
  -> memorist-core API
  -> Hot Scheduler runnable references
  -> PostgreSQL canonical ledger
       - sessions/messages/text_units
       - Jakobson runs/annotations/routes
       - candidates/evidence/memories/versions
       - durable jobs and projection/erasure outboxes
  -> FalkorDB rebuildable graph projection
       - Workspace/Project/Session/Message/TextUnit
       - JakobsonAnnotation/function/addressee/context/code
       - MemorySignalRoute/MemoryCandidate/Memory/MemoryVersion
  -> retrieval fusion with PostgreSQL fallback
  -> Memory Context Attachment
```

FalkorDB is never the source of truth. It is a rebuildable projection from
PostgreSQL and must not resurrect forgotten or quarantined rows during rebuild.

## Required Runtime

Full refuses accidental SQLite canonical storage:

```env
MEMORIST_RUNTIME_PROFILE=full
MEMORIST_CANONICAL_STORE=postgres
MEMORIST_POSTGRES_DSN=postgresql://memorist:memorist@postgres:5432/memorist
MEMORIST_GRAPH_BACKEND=falkordb
MEMORIST_FALKORDB_URL=redis://falkordb:6379
MEMORIST_HOT_SCHEDULER=in_memory
```

`MEMORIST_ALLOW_FULL_GRAPH_DEGRADED=true` is required if Full intentionally runs
without `graph_backend=falkordb`. Graph degradation is explicit in health and
diagnostics; it must not silently claim a certified graph path.

## Diagnostics

The health and diagnostics payloads expose:

```json
{
  "runtime_profile": "full",
  "canonical_store": "postgres",
  "graph_backend": "falkordb",
  "graph_status": "ok|degraded|down",
  "scheduler": "in_memory",
  "full_mode_certification": "passed|failed|not_run|degraded"
}
```

Commands:

```sh
curl http://localhost:8777/memcore/health
curl http://localhost:8777/memcore/diagnostics/daily
curl http://localhost:8777/memcore/scheduler/status
curl http://localhost:8777/memcore/graph/diagnostics
```

## Certification

Run:

```sh
python scripts/full_mode_check.py
```

The report is written to:

- `release/artifacts/full-mode-certification-report.ijson`
- `release/artifacts/full-mode-certification-report.md`

Skipped or manual-only gates do not count as passed. Full Mode can be
beta-supported only when all required external gates pass: PostgreSQL canonical
smoke, PostgreSQL job/outbox concurrency, scheduler preemption, import under
live traffic, FalkorDB projection, FalkorDB rebuild, graph retrieval, graph-down
fallback, graph forget/residue, SQLite-to-PostgreSQL migration, and full compose
smoke.

## Compose

```sh
docker compose -f docker-compose.full.yml up --build
```

The certification compose smoke starts local containers when Docker is
available:

```sh
python release/tests/full_compose_smoke.py
```

Set `MEMORIST_FULL_COMPOSE_SMOKE=false` only when intentionally skipping the
gate. If Docker is unavailable or the gate is skipped, Full remains an
experimental preview.

## Import and reconstruction runtime

Full Mode import is PostgreSQL-backed end to end. Import APIs store `import_runs`, staged artifacts,
issues, records, imported conversations, dry-run reports, mappings, progress, commit batches, and
per-message reconstruction state in PostgreSQL. Commit creates canonical workspaces, sessions, and
messages in PostgreSQL, preserving source IDs/timestamps and branch metadata in the message snapshot
and message mappings. The regular Full Mode session/message reads see imported conversations because
there is no separate SQLite canonical copy.

There is no silent Lite fallback: `runtime_profile=full` requires `canonical_store=postgres` and a
valid `MEMORIST_POSTGRES_DSN`. Any unsupported runtime/store pairing fails explicitly during import
runtime selection.
