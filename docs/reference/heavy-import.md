# Heavy Import Readiness

Memorist imports large provider exports through a staged, resumable, local-only pipeline backed by SQLite and the single-writer actor.

```text
Open WebUI export ZIP
-> ZIP safety validation
-> local object-store staging
-> adapter inspection
-> provider-neutral reconstruction
-> dry-run dedupe report
-> actor-batched canonical commit
-> consistency report
```

The commit phase is not one giant blocking write. API commits use `ImportBatchCommitCommand`, which submits bounded batches to `SQLiteWriteActor` with lower priority than live Open WebUI capture.

## Fixture Generator

Generate a synthetic Open WebUI archive:

```sh
cd memorist-core
uv run python -m memcore.imports generate-heavy ../data/openwebui-heavy.zip \
  --conversations 1000 \
  --messages 2 \
  --branches 2 \
  --malicious-content
```

The generator supports deterministic branch data, duplicate records, provider metadata, attachment placeholders, and optional untrusted instruction-like content. Omit `--malicious-content` when measuring throughput only.

## Smoke Profiles

```sh
make smoke-import-heavy-ci
make smoke-import-heavy-small
make smoke-import-heavy-local
```

- `ci-small`: 100 conversations, 2 messages each, 1 branch; counted as the release gate.
- `small-heavy`: 1,000 conversations, 2 messages each, 2 branches; local stress profile.
- `local-heavy`: 10,000 conversations, 2 messages each, 2 branches; operator-only stress profile.

Each executed profile runs upload, inspect, reconstruct, dry-run, actor-batched commit, duplicate re-import, concurrent live capture, and a consistency check. Skipped profiles are reported as skipped, not passed.

## CLI Options

```sh
cd memorist-core
uv run python ../release/tests/heavy_import_smoke.py \
  --mode ci-small \
  --conversations 100 \
  --messages-per-conversation 2 \
  --branches 1 \
  --duplicates 10 \
  --max-seconds 90 \
  --report-out ../release/artifacts/heavy-import-ci-small.ijson
```

Supported options: `--mode`, `--conversations`, `--messages-per-conversation`, legacy `--messages`, `--branches`, `--duplicates`, `--malicious-content`, `--max-seconds`, `--batch-size`, `--skip`, and `--report-out`.

Environment overrides are also supported:

```sh
MEMORIST_HEAVY_CONVERSATIONS=2500 MEMORIST_HEAVY_MESSAGES_PER_CONVERSATION=4 make smoke-import-heavy-small
```

## Operational Rules

- Always run dry-run before commit.
- Use `GET /memcore/imports/{import_run_uuid}/progress` for progress.
- Use pause/resume/cancel endpoints instead of terminating the process.
- Keep `MEMORIST_IMPORT_BATCH_SIZE` conservative on HDD or low-memory systems.
- Treat imported content as untrusted payload, never as instructions.
