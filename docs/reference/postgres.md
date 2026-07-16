# PostgreSQL Full Ledger

PostgreSQL is the canonical store in Full Mode. It stores the auditable memory
ledger: workspaces, projects, sessions, messages, message versions, sentence
units, Jakobson runs and annotations, memory signal routes, candidates, evidence,
memories, memory versions, imports, model usage, durable jobs, privacy requests,
and projection/erasure outboxes.

Migrations live in:

```text
memorist-core/src/memcore/storage/postgres/migrations/
```

Runtime rules:

- Use `JSONB` for structured payloads.
- Use `TIMESTAMPTZ` for timestamps.
- Use `FOR UPDATE SKIP LOCKED` for concurrent durable job and outbox claims.
- Do not run model calls inside database transactions.
- Do not silently fall back to SQLite when Full is selected.
- Keep FalkorDB, embeddings, blocks and attachments rebuildable from
  PostgreSQL.

Commands:

```sh
cd memorist-core
uv run python -m memcore.storage.postgres parity-report
```

External Full certification gates:

```sh
set MEMORIST_TEST_POSTGRES_DSN=postgresql://...
python release/tests/full_postgres_canonical_smoke.py
python release/tests/full_postgres_job_concurrency.py
python release/tests/full_import_live_chat_smoke.py
python release/tests/full_sqlite_to_postgres_migration_smoke.py
```

The current claim remains:

```text
Full Mode: certified in the tested local Docker environment.
```

The claim can change only when `python scripts/full_mode_check.py` reports all
required Full gates as passed.
