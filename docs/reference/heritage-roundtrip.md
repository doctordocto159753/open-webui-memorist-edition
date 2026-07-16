# Heritage Roundtrip

Heritage packages are offline, local, portable exports of Memorist canonical data. The final-polish release gate validates a rich roundtrip, not a shallow fixture.

```text
source SQLite DB
-> rich golden fixture
-> Heritage export ZIP
-> checksum and I-JSON verification
-> actor-backed restore into a fresh SQLite DB
-> FTS/projection rebuild
-> canonical table comparison
-> tamper-detection check
```

## Commands

```sh
make heritage-roundtrip
```

Manual workflow:

```sh
cd memorist-core
uv run python -m memcore.heritage verify path/to/heritage.zip
uv run python -m memcore.heritage restore path/to/heritage.zip \
  --db-path ./data/restored.sqlite \
  --dry-run
uv run python -m memcore.heritage compare path/to/heritage.zip \
  --db-path ./data/source.sqlite \
  --other-db-path ./data/restored.sqlite
```

## Golden Fixture Coverage

The fixture includes workspace, project, session, user/assistant/system/tool messages, message version, memory processing run, text unit, memory candidate, evidence, memory, memory version, memory-evidence link, active memory block, block version, block source, Memory Context Attachment, session hot cache, import run, import record, import mapping, privacy request, and erasure receipt.

The roundtrip asserts UUID and content-hash preservation for canonical rows, attachment source preservation, evidence lineage preservation, FTS rebuild health, object-store behavior, and that erased content is not resurrected.

## Tamper Policy

The gate mutates the exported package manifest and expects verification to fail. A package that restores after checksum tampering is a release blocker.

## What Is Compared

The comparator hashes canonical I-JSON rows from the tables included in the Heritage package. Schema migration metadata, writer idempotency records, runtime-only diagnostics, and rebuilt local projections are intentionally excluded from canonical comparison.
