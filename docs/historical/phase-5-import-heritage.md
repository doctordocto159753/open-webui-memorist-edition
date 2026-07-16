# Phase 5 — Import and Heritage

Phase 5 adds a local-only import staging pipeline and a portable Heritage export format. It does not add new memory extraction logic; imported provider data remains staged until an explicit dry-run and commit.

## Import Pipeline

The import pipeline is deliberately split into safe stages:

1. `upload` validates and stages a local ZIP archive under the object store.
2. `inspect` probes staged artifacts and records adapter candidates.
3. `reconstruct` normalizes provider conversations into a provider-neutral graph.
4. `dry-run` computes dedupe decisions and expected canonical writes.
5. `commit` writes sessions/messages and import mappings.

Provider-specific raw payloads, unknown fields, malformed records, and reconstruction warnings are preserved in staging tables. Canonical session/message rows are not created before commit.

## Supported Adapters

- ChatGPT export mapping DAGs.
- Claude conversation/message exports.
- Gemini/Google Takeout activity snapshots.
- Open WebUI chat history exports.
- Generic Memorist JSON conversations.
- Manual transcript JSON records.

Adapters are confidence-scored. Ambiguous detection creates import issues instead of silently selecting a destructive path.

## Archive Safety

ZIP staging rejects unsafe archives before extraction:

- absolute paths and path traversal
- symlink/device entries
- nested archives
- excessive compressed size, expanded size, file count, or compression ratio

Staged files are copied to generated object-store paths so provider filenames cannot control runtime paths.

## I-JSON and Dedupe

All staged payload columns ending in `_ijson` are validated before insert or update. Canonical hashes are computed from deterministic compact I-JSON. `None` values from provider exports are stripped before timestamp-sensitive I-JSON serialization so missing timestamps stay absent rather than invalid.

Dedupe uses source mappings and canonical fingerprints. Re-running the same archive reports already-mapped conversations instead of duplicating canonical sessions.

## Heritage Package

Heritage export creates an offline-verifiable ZIP package:

- `manifest.ijson`
- `checksums.sha256`
- `data/*.ijsonl`
- `schemas/*.json`
- `objects/`
- `reports/`

Verification checks package paths, manifest I-JSON, data I-JSONL, and SHA-256 checksums without contacting any external service. Restore supports dry-run first and writes only trusted known tables when explicitly executed.

## CLI

```sh
uv run python -m memcore.imports inspect path/to/export.zip
uv run python -m memcore.heritage verify path/to/heritage.zip
uv run python -m memcore.heritage inspect path/to/heritage.zip
uv run python -m memcore.heritage restore path/to/heritage.zip --db-path ./data/restored.sqlite --dry-run
```

All commands are local-only and do not send telemetry.
