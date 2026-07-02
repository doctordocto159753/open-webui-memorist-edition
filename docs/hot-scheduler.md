# Hot Scheduler

The hot scheduler protects live chat and privacy work from background starvation
in Full Mode. It stores only runnable references in memory; durable payloads and
state remain in PostgreSQL jobs/outboxes.

Lanes and default priorities:

```text
critical_privacy: 110
live_chat_capture: 100
preflight_persist: 95
assistant_capture: 90
memory_extraction: 60
import_commit: 40
import_reconstruction: 30
graph_projection: 25
embedding_index: 25
block_rebuild: 20
maintenance: 10
```

Expected behavior:

- Privacy preempts import and graph projection.
- Live chat capture runs before additional low-priority batches.
- Import and reconstruction yield after bounded batches.
- Scheduler restart must not lose durable jobs because PostgreSQL owns payloads.
- Metrics expose lane depth, oldest age, p95 wait, low-priority yield and
  backpressure state.

Status:

```sh
curl http://localhost:8777/memcore/scheduler/status
```

External smoke:

```sh
set MEMORIST_TEST_POSTGRES_DSN=postgresql://...
python release/tests/full_scheduler_live_chat_preemption.py
```

Skipped scheduler smoke blocks Full beta support, but does not affect Lite Mode.
