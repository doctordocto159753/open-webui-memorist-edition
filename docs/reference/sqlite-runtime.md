# SQLite Runtime

SQLite is the authoritative local store for `v0.2.0-beta.3`. The default runtime does not require FalkorDB, Qdrant, cloud queues, or external storage.

## Connection Policy

The SQLite connector enables:

- foreign keys;
- WAL mode;
- busy timeout;
- local retry for transient busy/locked errors.

Hot Open WebUI writes use the `SQLiteWriteActor` through `WriteGateway`. The actor owns a single writer connection and serializes:

- Open WebUI session resolution writes;
- Open WebUI message capture writes;
- idempotency record writes.
- WP02 coverage plans, proposal reservations, and candidate/evidence links.

Read-heavy routes open short-lived local SQLite connections.

## WP02 audit and replay

Migration `0038_message_first_semantics.sql` adds the Message semantics ledger;
the preceding `0037_semantic_coverage_audit.sql` adds
`semantic_coverage_runs`, `semantic_coverage_items`, and
`semantic_candidate_links`. These rows contain hashes, versions, dispositions,
lineage UUIDs, and reason codes—not raw evidence, propositions, or bounded
context text. Plan insertion uses `BEGIN IMMEDIATE`; migration SQL and its
`schema_migrations` record commit atomically.

A linked replay verifies the stored candidate and deterministic evidence, then
revalidates the current processing run's gate, route, and privacy authority.
The eventual candidate UUID equals the proposal UUID. A payload mismatch or
same-text new message version fails closed instead of producing a duplicate.

## Diagnostics

```sh
curl http://localhost:8777/memcore/diagnostics/write-actor
```

Important fields:

- `started`: writer thread has been started;
- `queue_depth`: pending write commands;
- `submitted_count`: accepted commands;
- `completed_count`: completed commands;
- `error_count`: command failures;
- `last_queue_wait_ms`: most recent queue wait;
- `last_transaction_ms`: most recent command duration.

`/memcore/diagnostics/daily` also reports storage size and warns when write depth reaches the configured import backpressure threshold.

## Operational Rules

- Keep the database on a local disk, not a network share.
- Do not run `VACUUM` on the hot path.
- Use the backup command instead of copying an active database file.
- Pause large imports if daily chat capture starts queuing.

Backup:

```sh
cd memorist-core
uv run python -m memcore.reliability backup --out backup.sqlite
```

Maintenance:

```sh
uv run python -m memcore.reliability wal-checkpoint
uv run python -m memcore.reliability secure-delete-check
```
