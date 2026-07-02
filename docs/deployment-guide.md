# Memorist OpenWebUI Deployment Guide

This guide explains how to run, configure, validate, package, and upload Memorist OpenWebUI for local use.

## 1. Choose Deployment Mode

Use **Lite** unless you specifically need graph experiments.

| Mode | Use case | Services | Notes |
| --- | --- | --- | --- |
| Lite | normal local use / weak systems | `memorist-core`, `open-webui`, SQLite volumes | recommended default |
| Standard | Python/dev workflow | `memorist-core` via `uv`, optional Open WebUI | best for development |
| Full | graph-capable local stack | Lite + FalkorDB | heavier, optional |

## 2. Prerequisites

For Python development:

- Python `3.12`
- `uv`

For local package:

- Docker
- Docker Compose
- free local ports `8777` and `3000`

On Windows, use PowerShell for Python/dev commands. The package shell scripts are Bash scripts; run them from Git Bash, WSL, or a Unix-like shell. If you stay in PowerShell, run equivalent `docker compose` commands directly.

## 3. Python Development Run

```powershell
cd F:\Memorist\V1code\memorist-openwebui\memorist-core
python -m uv sync --dev
python -m uv run uvicorn memcore.main:app --host 0.0.0.0 --port 8777 --reload
```

Check health:

```powershell
curl http://localhost:8777/memcore/health
curl http://localhost:8777/memcore/version
curl http://localhost:8777/memcore/config/effective
```

Expected health:

```json
{
  "status": "ok",
  "service": "memorist-core",
  "local_only": true
}
```

## 4. Local Package Run

The assembled release candidate is:

```text
release/rc/memorist-openwebui-0.2.0-beta.1.zip
```

Verify checksum:

```powershell
cd F:\Memorist\V1code\memorist-openwebui
$expected = (Get-Content release\rc\memorist-openwebui-0.2.0-beta.1.sha256).Split()[0]
$actual = (Get-FileHash release\rc\memorist-openwebui-0.2.0-beta.1.zip -Algorithm SHA256).Hash.ToLower()
$expected -eq $actual
```

Extract:

```powershell
Expand-Archive release\rc\memorist-openwebui-0.2.0-beta.1.zip -DestinationPath release\rc\extracted -Force
cd release\rc\extracted\memorist-openwebui-0.2.0-beta.1\release\memorist-openwebui
Copy-Item .env.example .env
```

Run Lite with Docker Compose directly from PowerShell:

```powershell
docker compose --profile lite -f compose.yml up -d --build memorist-core open-webui
```

Run Full:

```powershell
docker compose --profile full -f compose.yml up -d --build
```

Open:

- Open WebUI: `http://localhost:3000`
- Memorist Core: `http://localhost:8777/memcore/health`

Stop:

```powershell
docker compose -f compose.yml down
```

## 5. Environment Configuration

Start from `.env.example`.

```env
MEMORIST_LOCAL_ONLY=true
MEMORIST_MODE=lite
MEMORIST_CORE_URL=http://localhost:8777
MEMORIST_DB_PATH=./data/memorist.sqlite
MEMORIST_OBJECT_STORE_PATH=./data/objects
MEMORIST_IMPORT_STAGING=./data/imports
MEMORIST_EXPORT_DIR=./data/exports
MEMORIST_GRAPH_BACKEND=disabled
MEMORIST_PREFLIGHT_ENABLED=true
MEMORIST_PREFLIGHT_TIMEOUT_MS=1200
MEMORIST_ATTACHMENT_TOKEN_BUDGET=1800
MEMORIST_FAIL_OPEN=true
MEMORIST_ENABLED=true
MEMORIST_DEBUG=false
```

For Compose package paths inside containers:

```env
MEMORIST_DB_PATH=/data/memorist.sqlite
MEMORIST_OBJECT_STORE=/objects
MEMORIST_OBJECT_STORE_PATH=/objects
MEMORIST_IMPORT_STAGING=/imports
MEMORIST_EXPORT_DIR=/exports
MEMORIST_CORE_URL=http://memorist-core:8777
```

Do not place provider API keys in `.env` unless a future secure secret-storage feature is explicitly added. Configure model providers in Open WebUI Admin Settings → Connections.

## 6. Open WebUI Integration

Integration files:

```text
open-webui-integration/memorist/filter/memorist_memory_filter.py
open-webui-integration/memorist/function/memorist_status_function.py
open-webui-integration/memorist/shared/
```

The Filter:

1. resolves a Memorist session;
2. captures inbound user messages;
3. calls `/memcore/preflight`;
4. inserts memory context as a separate `memorist_context` system-like message;
5. preserves the original user prompt;
6. captures outbound assistant response;
7. deduplicates repeated callback events;
8. fails open if Memorist Core is unavailable.

Security rule: Open WebUI Filters and Functions execute Python on the server. Install only from this trusted local package.

## 7. First-Run Checklist

1. Start Lite.
2. Confirm `GET /memcore/health` returns `local_only=true`.
3. Open Open WebUI.
4. Configure model provider in Open WebUI.
5. Install/enable Memorist Filter.
6. Install/enable Memorist status Function.
7. Start a chat.
8. Confirm chat works even if Memorist is down.
9. Re-enable Memorist and confirm preflight metadata appears.
10. Run smoke tests.

## 8. Validation Commands

Core:

```powershell
cd F:\Memorist\V1code\memorist-openwebui\memorist-core
python -m uv run ruff check .
python -m uv run mypy src/memcore
python -m uv run pytest -q
```

Open WebUI integration contract:

```powershell
python -m uv run pytest ..\open-webui-integration\memorist\tests -q
```

Eval:

```powershell
python -m uv run python -m memcore.eval run --dataset src/memcore/eval/fixtures/basic.ijsonl
```

Performance smoke:

```powershell
python -m uv run python -m memcore.performance perf-smoke --profile lite
```

Reliability:

```powershell
python -m uv run python -m memcore.reliability check
```

Release smoke from repo root:

```powershell
bash -lc "make UV='python.exe -m uv' PYTHON=python.exe smoke-daily"
python -m release.tests.report --manifest release/test_manifest.ijson --external-gates-passed
```

Compose config:

```powershell
docker compose -f release\memorist-openwebui\compose.yml config --quiet
```

## 9. Import Workflow

Inspect archive:

```powershell
cd memorist-core
python -m uv run python -m memcore.imports inspect path\to\export.zip
```

API flow:

1. upload/stage archive;
2. inspect adapters;
3. reconstruct conversations;
4. dry-run;
5. review duplicate/cost/security warnings;
6. commit;
7. optionally run memory processing.

Imported instruction-like content remains untrusted data.

## 10. Heritage Export / Restore

Verify:

```powershell
python -m uv run python -m memcore.heritage verify path\to\heritage.zip
```

Restore dry-run:

```powershell
python -m uv run python -m memcore.heritage restore path\to\heritage.zip --db-path .\data\restored.sqlite --dry-run
```

Use dry-run first. Do not restore over active production data without a backup.

## 11. Backup and Maintenance

Safe SQLite backup:

```powershell
python -m uv run python -m memcore.reliability backup --out backup.sqlite
```

WAL checkpoint:

```powershell
python -m uv run python -m memcore.reliability wal-checkpoint
```

Secure delete check:

```powershell
python -m uv run python -m memcore.reliability secure-delete-check
```

`VACUUM` should not run in the hot path.

## 12. Packaging for Upload

Regenerate release manifest:

```powershell
python installer\scripts\build_release_manifest.py --out release\build
```

Regenerate RC package:

```powershell
python installer\scripts\assemble_rc.py
```

Verify RC checksum:

```powershell
$expected = (Get-Content release\rc\memorist-openwebui-0.2.0-beta.1.sha256).Split()[0]
$actual = (Get-FileHash release\rc\memorist-openwebui-0.2.0-beta.1.zip -Algorithm SHA256).Hash.ToLower()
$expected -eq $actual
```

Recommended files to upload to GitHub Release:

- `release/rc/memorist-openwebui-0.2.0-beta.1.zip`
- `release/rc/memorist-openwebui-0.2.0-beta.1.sha256`
- `release/rc/RELEASE_NOTES.md`
- `release/rc/KNOWN_LIMITATIONS.md`
- `release/rc/SECURITY.md`
- `release/rc/HANDOFF.md`
- `release/artifacts/release-smoke-report.ijson`

Recommended source upload:

```powershell
git add .
git commit -m "Initial Memorist OpenWebUI release candidate"
git branch -M main
git remote add origin <repo-url>
git push -u origin main
```

## 13. Troubleshooting

### Memorist Core unavailable

- Check port `8777`.
- Run `/memcore/health`.
- Check `.env` path variables.
- Run `python -m uv run python -m memcore.reliability check`.

### Open WebUI starts but no memory

- Confirm Filter is installed and enabled.
- Confirm `MEMORIST_CORE_URL` is local and reachable from Open WebUI runtime.
- Confirm `MEMORIST_PREFLIGHT_ENABLED=true`.
- Check status Function output.

### Import fails

- Archive may contain unsafe paths, nested archives, or excessive compression ratio.
- Run inspect first.
- Review import issues before commit.

### Full mode graph unavailable

- Use Lite mode.
- Set `MEMORIST_GRAPH_BACKEND=disabled`.
- Check FalkorDB container health if using Full.

## 14. Release Readiness State

Current state:

- Core tests pass.
- Integration tests pass.
- Daily local API smoke passes.
- Eval baseline passes.
- Security tests pass.
- Performance smoke passes.
- Reliability check passes.
- Release smoke passes.
- Compose config validates.
- RC zip and checksum are generated.

Remaining before public production release:

- Run actual Lite container startup on target machine.
- Run actual Full graph startup if Full is advertised.
- Review dependency licenses.
- Run vulnerability scan/SBOM tooling if available.
- Manually install Filter/Function into the exact target Open WebUI version.
