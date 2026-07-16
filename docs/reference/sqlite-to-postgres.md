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
receipts, and import lineage where source and target tables exist.

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
