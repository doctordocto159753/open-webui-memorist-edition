# PostgreSQL Full Ledger

PostgreSQL is the canonical store in Full Mode. It stores the auditable memory
ledger: workspaces, projects, sessions, messages, message versions, sentence
units, Jakobson runs and annotations, memory signal routes, candidates, evidence,
memories, memory versions, imports, model usage, durable jobs, privacy requests,
and projection/erasure outboxes.

WP02 migration `0024_semantic_coverage_audit.sql` adds content-free semantic
coverage runs/items and candidate links. It also aligns candidate evidence
roles/support types with SQLite. Coverage-plan insert, candidate/evidence
creation, and link transition use PostgreSQL transactions and conflict checks;
a reservation can survive a crash, while a candidate cannot commit without
its link. Route, gate, privacy, message version, semantic contract, and policy
versions are checked again on replay.

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
