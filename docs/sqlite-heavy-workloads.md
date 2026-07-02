# SQLite Heavy Workloads

Memorist remains local-first in P2. Heavy import, Heritage restore, privacy
forget, and projection rebuilds must not require cloud queues or external
databases.

## Writer Actor

`SQLiteWriteActor` owns the hot write connection. It now provides:

- priority queueing
- queue depth by priority
- oldest queued command age
- p50/p95 queue wait and transaction duration
- command throughput estimate
- busy retry counters
- oversized command rejection
- idempotency replay for commands that opt in

High-priority Open WebUI capture commands can run between low-priority import
batches. This is the main protection for daily-use responsiveness during heavy
local imports.

## Remaining Direct Writes

Not every repository method is actor-mediated. P2 explicitly audits and
documents remaining direct writes. Allowed categories are:

- domain repository internals used by actor commands
- bounded control-plane updates such as pause/resume/cancel
- local projection maintenance such as FTS rebuild
- governance correction state transitions
- security/report audit rows
- SQLite backup, checkpoint, vacuum, and migration operations

The release smoke `make consistency-check` fails if a new direct-write location
appears without an explicit justification.

## Tuning

Use these settings first:

```env
MEMORIST_IMPORT_BATCH_SIZE=100
MEMORIST_IMPORT_MAX_WRITE_QUEUE_DEPTH=500
MEMORIST_IMPORT_LOW_PRIORITY=true
```

Lower `MEMORIST_IMPORT_BATCH_SIZE` on slow disks or when Open WebUI capture
latency rises. Increase it only after `make smoke-import-heavy-ci` and
`make consistency-check` pass on the target machine.

