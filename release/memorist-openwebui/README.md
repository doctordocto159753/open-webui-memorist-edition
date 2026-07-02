# Memorist OpenWebUI Local Package

This package runs Open WebUI as the parent chat UI and Memorist Core as a complementary local memory service. Release state: `v0.2.0-beta.1 candidate`. Use Lite mode for the validated local path. The packaged Open WebUI image target is pinned to `ghcr.io/open-webui/open-webui:v0.9.6` unless `OPENWEBUI_IMAGE` is explicitly overridden.

## What You Get

- Open WebUI container
- Memorist Core FastAPI container
- SQLite-backed local memory engine
- local object store volumes
- optional FalkorDB service for Full mode
- trusted Open WebUI Filter/Function bundle
- documented Open WebUI Filter contract and payload fixtures
- package manifest and forbidden-file scanner
- SQLite writer diagnostics and real daily-use smoke
- import pause/resume/cancel progress endpoints
- doctor, backup, restore, logs, and reset scripts
- install/security/import/privacy/upgrade docs

## Start Lite

```bash
cp .env.example .env
scripts/start-lite.sh
```

Open:

- Open WebUI: `http://localhost:3000`
- Memorist Core: `http://localhost:8777/memcore/health`

Lite is the recommended default. It uses SQLite and does not require graph services or embeddings.

## Start Full

```bash
cp .env.example .env
scripts/start-full.sh
```

Full mode starts optional graph services, uses more memory, and remains an experimental preview until external PostgreSQL/FalkorDB gates pass.

## Install Memorist Into Open WebUI

Install these trusted server-side integration files according to your Open WebUI deployment:

```text
open-webui-integration/memorist/filter/memorist_memory_filter.py
open-webui-integration/memorist/function/memorist_status_function.py
open-webui-integration/memorist/shared/
```

Security warning: Open WebUI Filters and Functions execute Python on the server. Install only from this trusted package.

## Configure Model Providers

Do not place provider credentials in Memorist files. Configure model providers in Open WebUI Admin Settings -> Connections.

## Run Doctor

```bash
scripts/doctor.sh lite
```

Doctor checks Docker, Compose, writable folders, ports, health endpoints, local-only mode, and graph status.

## Daily Smoke

From the source repository root, run:

```bash
make smoke-daily
```

The smoke verifies Memorist Core health, base APIs, Open WebUI capture, adaptive budget calculation, and diagnostics against a temporary local SQLite database.

## Backup

```bash
scripts/backup.sh
```

Backups use the SQLite backup API; do not copy a live WAL database manually.

## Restore

```bash
scripts/restore.sh path/to/heritage.zip
```

Restore runs dry-run first unless explicit confirmation is provided.

## Data Volumes

- `openwebui-data`
- `memorist-data`
- `memorist-objects`
- `memorist-import-staging`
- `memorist-exports`
- `falkordb-data` in Full mode

## Local-only Policy

Memorist defaults to local-only mode, no telemetry, and no default API keys.
