# Handoff

## What This Package Contains

- Memorist Core FastAPI service and SQLite migrations
- Open WebUI trusted Filter/Function integration bundle
- Lite/Full Docker Compose local package
- import/export and Heritage tooling
- evaluation, security, performance, reliability, and release smoke tooling
- RC zip and checksum

## Run Lite

```bash
cd release/memorist-openwebui
cp .env.example .env
scripts/start-lite.sh
```

Lite is the default path. It uses SQLite and local volumes.

## Run Full

```bash
cd release/memorist-openwebui
cp .env.example .env
scripts/start-full.sh
```

Full starts optional graph services and remains an experimental preview. Use it only for local evaluation until PostgreSQL/FalkorDB external gates pass.

## Configure Model Providers

Use Open WebUI Admin Settings → Connections. Do not store provider API keys inside Memorist integration files or release docs.

## Enable Memorist

Install and enable:

```text
open-webui-integration/memorist/filter/memorist_memory_filter.py
open-webui-integration/memorist/function/memorist_status_function.py
open-webui-integration/memorist/shared/
```

The Filter preserves the user prompt and inserts memory context separately.

## Import Old Exports

Use inspect → reconstruct → dry-run → commit. Imported instruction-like content remains untrusted data.

## Run Diagnostics

```bash
release/memorist-openwebui/scripts/doctor.sh lite
```

Also check:

```text
release/artifacts/release-smoke-report.ijson
release/memorist-openwebui/VERSION.ijson
```

## Backup

Use:

```bash
release/memorist-openwebui/scripts/backup.sh
```

## Restore

Use:

```bash
release/memorist-openwebui/scripts/restore.sh path/to/heritage.zip
```

Review dry-run output before destructive restore.

## Bug Reports

Attach:

- exact steps
- sanitized logs
- `VERSION.ijson`
- release smoke report
- operating system and Docker versions
- Open WebUI version/tag

Do not attach:

- raw SQLite memory DB
- provider exports
- API keys
- `.env` files with secrets
- raw private conversations

## Upload Files

Upload these as GitHub release artifacts:

```text
release/rc/memorist-openwebui-0.2.0-beta.1.zip
release/rc/memorist-openwebui-0.2.0-beta.1.sha256
release/rc/RELEASE_NOTES.md
release/rc/KNOWN_LIMITATIONS.md
release/rc/SECURITY.md
release/rc/HANDOFF.md
release/artifacts/release-smoke-report.ijson
```
