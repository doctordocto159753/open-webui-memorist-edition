# Installation

This guide covers running Memorist locally: the Windows-first one-click
release path, the cross-platform script path, and what to configure on first
run. Developer setup from source lives in [DEVELOPMENT.md](DEVELOPMENT.md).

> **Status:** early public alpha. **Lite** uses SQLite. **Full** uses PostgreSQL
> + FalkorDB and is certified in the tested local Docker environment (11/11
> external gates and Windows 11 one-click/lifecycle smoke).

## Requirements

| Need | Detail |
| --- | --- |
| OS | Windows 10/11 first-class; macOS/Linux via PowerShell 7 or the bash scripts |
| Container engine | **Docker Desktop** (or Docker Engine + Compose on Linux), installed and running |
| Disk | Lite: ~4 GB; Full: ~8 GB and 6 GB RAM recommended |
| Network | Only to pull images — and, optionally, to reach a remote model provider you configure |

**Docker Desktop is currently required for the one-click release path.** The
installer detects when Docker is missing or not running and prints exact
instructions instead of failing cryptically. A Dockerless Lite build is a
possible future direction, **not** part of this release.

Download Docker Desktop: <https://www.docker.com/products/docker-desktop/>

## Release package layout

Download and unzip the release package (`memorist-openwebui-<version>.zip`)
into a folder you own:

```text
memorist-openwebui/
  Memorist.cmd                 ← double-click to install (Windows)
  Install-Memorist.ps1         ← setup wizard
  Start-Memorist.ps1 / Stop-Memorist.ps1 / Restart-Memorist.ps1
  Show-Memorist-Logs.ps1
  Reset-Memorist-Data.ps1 / Uninstall-Memorist.ps1
  compose.yml                  ← common release orchestration
  compose.lite.yml / compose.full.yml
  runtime/                     ← self-contained Core + integration build inputs
  .env.example                 ← config template (copied to .env on install)
  README-LOCAL.md
  scripts/                     ← shared PowerShell module + bash equivalents
  docs/                        ← packaged install/security/upgrade notes
  checksums.sha256             ← integrity manifest (sha256sum -c)
```

## Install (Windows one-click)

1. Double-click **`Memorist.cmd`**. It launches the wizard through PowerShell
   using `-ExecutionPolicy Bypass` for that one process only — nothing
   system-wide changes.
2. The wizard explicitly asks Lite or Full, then checks Docker, ports (`3000`, `8777`), and disk; creates local
   data folders; generates a private `.env` with strong session secrets; asks
   how memory extraction should run (see below); starts the services; waits
   for health; and opens <http://localhost:3000>.

From a terminal instead:

```powershell
.\Install-Memorist.ps1                            # interactive Lite/Full choice
.\Install-Memorist.ps1 -Mode full -NonInteractive -NoBrowser
.\Install-Memorist.ps1 -Mode lite -DryRun -NonInteractive
```

Re-running the wizard is safe: existing secrets in `.env` are preserved.
macOS/Linux: the same scripts run under PowerShell 7
(`pwsh ./Install-Memorist.ps1`), or use the bash scripts
(`scripts/start-lite.sh`, `scripts/doctor.sh`, …).

## Memory processing and API keys

The wizard offers three choices for the memory-extraction role:

| Option | Needs API key? | Where conversation-derived text goes |
| --- | --- | --- |
| **Local deterministic** (default) | No | Stays fully on your machine |
| **OpenAI-compatible remote** | Yes | Memory text may be sent to that provider |
| **Skip for now** | No | Configure later in the UI |

### How key storage works (and why)

The in-app **Processing Nodes** page (Settings → Memorist) never accepts, stores,
returns, or logs plaintext API keys — it stores an **environment-variable
reference** (a name), and the backend resolves the value from its own process
environment. The installer is the local-user bridge for that model:

- you type the key once, in the terminal, during install;
- it is written **only** to the local, git-ignored, ACL-restricted `.env` and
  injected into the `memorist-core` container;
- in the UI you reference the variable **name**, e.g.
  `MEMORIST_MEMORY_EXTRACTION_API_KEY` — never the value;
- the key is never echoed back, never logged, never
  stored in the database, never returned by any API.

Role variables the installer manages:

```text
MEMORIST_MEMORY_EXTRACTION_API_KEY
MEMORIST_HIGH_CONFIDENCE_EXTRACTION_API_KEY
MEMORIST_EMBEDDING_API_KEY
MEMORIST_PRIVACY_SENSITIVITY_API_KEY
MEMORIST_IMPORT_RECONSTRUCTION_API_KEY
```

Remote endpoints additionally require an explicit **privacy acknowledgement**
inside Memory Setup before they can become a role default — the installer does
not bypass that consent step. See [SECURITY.md](../SECURITY.md).

## First run

After the browser opens:

1. Create/sign in to your Open WebUI account (the first account is admin).
2. Open **Settings → Memorist → Memory Setup**. A fresh Lite install reports
   **Ready — local fallback available**; configuring a remote provider is
   optional, not required.
3. Chat normally. The **Memory On / Memory Off** switch sits next to the
   composer; turns that used memory show a **"Memory used"** attachment panel.

Health endpoints if you want to check by hand:

```text
http://localhost:3000/health              Open WebUI
http://localhost:8777/memcore/health      Memorist Core
http://localhost:8777/memcore/diagnostics/daily
```

## Lite vs Full

| | Lite (default) | Full |
| --- | --- | --- |
| Canonical store | SQLite | PostgreSQL |
| Graph projection | disabled | FalkorDB |
| Embeddings | optional/disabled | optional |
| Services | `memorist-core`, `open-webui` | + `postgres`, `falkordb` |
| Resource use | low | higher |
| Scheduler | disabled | `in_memory` |
| Required features | adapter, worker/import/attachment/blocks/profile/forget | same + graph projection |
| Host ports | loopback UI/Core only | same; PostgreSQL/FalkorDB internal only |
| One-click reliability | validated | certified in the tested local Docker environment |

The Full certification report passed every external gate. Lite and Full
share the same canonical semantic pipeline, so switching later does not change
what the memory machine decides.

## Everyday commands

| Task | Windows | bash |
| --- | --- | --- |
| Start | `.\Start-Memorist.ps1` | `scripts/start-lite.sh` |
| Stop (keeps data) | `.\Stop-Memorist.ps1` | `scripts/stop.sh` |
| Restart | `.\Restart-Memorist.ps1` | stop + start |
| Logs | `.\Show-Memorist-Logs.ps1` | `scripts/logs.sh` |
| Health/doctor | — | `scripts/doctor.sh lite` |
| Reset all memory data | `.\Reset-Memorist-Data.ps1` | `scripts/reset-dev-data.sh --yes-i-understand` |
| Uninstall | `.\Uninstall-Memorist.ps1` | `docker compose down` |

Reset requires typing `DELETE`. Uninstall preserves your data volumes unless
you pass `-PurgeData`. Nothing deletes memory silently.

## Where things live

- **Config + secrets:** `.env` in the package folder (git-ignored, restricted
  to your user — keep it private).
- **Lite canonical data:** SQLite in the project-scoped `memorist-data` volume.
- **Full canonical data:** PostgreSQL in `memorist-postgres-data`; FalkorDB in
  `falkordb-data` is rebuildable and non-canonical.
- **Logs:** `Show-Memorist-Logs.ps1` / `scripts/logs.sh`.

## Backup and upgrade

Your data lives in Docker volumes and local folders, so replacing package
files does not delete memories.

- **Backup:** `scripts/backup.sh` (uses the SQLite backup API — do not copy a
  live WAL database by hand), or Heritage export for a portable, verifiable
  package. See [reference/backup-restore.md](reference/backup-restore.md).
- **Upgrade:** stop Memorist, copy `.env` to the new extracted package, rerun
  the installer in the persisted mode, then start. Stable volume names retain
  accounts and data even if the extraction path changes.
- **Restore:** `scripts/restore.sh path/to/heritage.zip` (dry-run first).

## Environment configuration

`.env.example` documents every variable. The defaults are local-safe:

```text
MEMORIST_LOCAL_ONLY=true          # local-only mode; false is rejected
MEMORIST_MODE=lite                # lite | full
MEMORIST_GRAPH_BACKEND=disabled   # falkordb in Full
MEMORIST_FAIL_OPEN=true           # chat survives memory outages
OPEN_WEBUI_PORT=3000
MEMORIST_PORT=8777
```

The installer generates `WEBUI_SECRET_KEY`,
`MEMORIST_ACTOR_ASSERTION_SECRET`, and `MEMORIST_ACTOR_SERVICE_TOKEN` with
cryptographically strong values. Do not commit `.env` (it is git-ignored) and
do not reuse the example placeholders in a shared deployment.

## Known limitations of this release

- Docker Desktop (or Docker Engine + Compose) is required; there is no native
  Dockerless installer.
- CI validates installer static checks, compose configuration, and dry-run —
  not every Windows desktop configuration. Report installer issues with the
  wizard output attached.
- Full certification is environment-specific, not a production-readiness or
  security-audit claim.
- There is no signed native `.msi`/`.exe` installer yet; Windows SmartScreen
  may warn about the script package.

If anything fails, see [TROUBLESHOOTING.md](TROUBLESHOOTING.md).
