# Consistency Checker

The consistency checker verifies local SQLite integrity and Memorist-specific
invariants after import, restore, forget, and recovery operations.

## Checks

- SQLite `PRAGMA quick_check`
- SQLite `PRAGMA foreign_key_check`
- canonical row references between sessions, messages, memories, evidence,
  retrieval candidates, blocks, and privacy requests
- duplicate import mappings
- FTS projection count drift
- stale running jobs

## Commands

```sh
make consistency-check
```

Direct CLI:

```sh
cd memorist-core
uv run python -m memcore.reliability.consistency check \
  --db-path ./data/memorist.sqlite \
  --json-output ./data/reports/consistency.ijson
uv run python -m memcore.reliability.consistency repair --db-path ./data/memorist.sqlite
```

The checker writes both compact I-JSON and Markdown reports when an output path
is provided. Safe repair does not fabricate missing content; it only marks
stale operational rows or rebuilds local projections.

## Direct-Write Audit

P2 includes a static audit of direct write locations. Remaining direct writes are
allowed only when they are actor-internal, bounded control-plane operations,
repository internals, or local maintenance/projection routines with an explicit
justification.

