# SQLite to PostgreSQL Migration

Back up the SQLite database before migration. SQLite remains the Lite ledger;
PostgreSQL becomes the Full ledger after migration.

Dry run:

```sh
cd memorist-core
uv run python -m memcore.migrate sqlite-to-postgres \
  --sqlite ../data/memorist.sqlite \
  --postgres "$MEMORIST_POSTGRES_DSN" \
  --dry-run
```

Commit:

```sh
uv run python -m memcore.migrate sqlite-to-postgres \
  --sqlite ../data/memorist.sqlite \
  --postgres "$MEMORIST_POSTGRES_DSN" \
  --commit
```

Verify:

```sh
uv run python -m memcore.migrate sqlite-to-postgres \
  --sqlite ../data/memorist.sqlite \
  --postgres "$MEMORIST_POSTGRES_DSN" \
  --verify
```

The migration is expected to preserve UUIDs, messages, message versions, text
units, Jakobson annotations, signal routes, candidates, memories, memory
versions, evidence, prompt execution runs, model profiles, usage events, privacy
receipts, import lineage, semantic coverage runs/items, and proposal/candidate
links where source and target tables exist.

Verification compares canonical mapped content digests as well as row counts.
An existing PostgreSQL primary key is accepted only when every mapped value is
identical; `ON CONFLICT` is not treated as a successful copy by itself. The
schema parity report fails for a missing WP02 table, column, or weakened closed
constraint.

After commit:

1. Run verification.
2. Rebuild FalkorDB only from active PostgreSQL rows.
3. Verify forgotten/quarantined content is not resurrected.
4. Rebuild embeddings if enabled.
5. Run `python release/tests/full_sqlite_to_postgres_migration_smoke.py`.

Full remains:

```text
Full Mode: certified in the tested local Docker environment.
```

until migration smoke and every other Full gate pass.
