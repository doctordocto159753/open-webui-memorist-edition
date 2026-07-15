# Windows Local Install (One-Click)

Memorist is a local-first memory edition of Open WebUI. This guide covers the
Windows-first, Docker-backed one-click install for **non-developers**: no Git,
Python, or `uv` required.

> **Status:** alpha/beta. **Lite** mode is the validated one-click path. **Full**
> mode (graph services) is an advanced preview.

## Requirements

| Need | Detail |
| --- | --- |
| OS | Windows 10 / 11 (PowerShell 7 on macOS/Linux also works — see below) |
| Container engine | **Docker Desktop**, installed and **running** |
| Disk | ~5 GB free for images + data |
| Network | Only to pull images and (optionally) reach a remote model provider |

Docker Desktop download: <https://www.docker.com/products/docker-desktop/>. The
installer detects when Docker is missing or not started and prints exact
instructions instead of a stack trace.

## Get the package

Download and unzip the release package (`memorist-openwebui-<version>.zip`) into
a folder you own, e.g. `C:\Users\<you>\Documents\Memorist`. Package layout:

```text
memorist-openwebui/
  Memorist.cmd                 <- double-click to install
  Install-Memorist.ps1         <- setup wizard
  Start-Memorist.ps1
  Stop-Memorist.ps1
  Restart-Memorist.ps1
  Reset-Memorist-Data.ps1
  Uninstall-Memorist.ps1
  Show-Memorist-Logs.ps1
  compose.yml                  <- release orchestration
  .env.example                 <- config template (copied to .env on install)
  README-LOCAL.md
  scripts/                     <- MemoristCommon.psm1 + bash helpers
  docs/                        <- install/security/privacy/upgrade docs
  checksums.sha256             <- integrity manifest
```

## Install

**Double-click `Memorist.cmd`.** It launches the wizard through PowerShell using
`-ExecutionPolicy Bypass` **for that one process only** — nothing system-wide is
changed. Prefer the terminal? Right-click the folder → *Open in Terminal*, then:

```powershell
.\Install-Memorist.ps1              # Lite (recommended)
.\Install-Memorist.ps1 -Mode full   # Advanced preview
.\Install-Memorist.ps1 -DryRun      # Validate only — writes nothing, starts nothing
```

The wizard:

1. checks Docker, required ports (`3000`, `8777`), and free disk;
2. creates local `data/ objects/ imports/ exports/ logs/` folders;
3. generates a private `.env` with strong `WEBUI_SECRET_KEY` and actor secrets;
4. asks how memory extraction should run (below);
5. starts the containers (`docker compose ... up -d --build`);
6. waits for health, then opens <http://localhost:3000>.

Re-running the wizard is safe: existing secrets in `.env` are preserved.

## Memory processing & API keys

The wizard offers three choices for the extraction role:

1. **Local deterministic** — no API key, fully local. Default and safest.
2. **OpenAI-compatible remote** — enter an API key; it is written **only** to the
   local `.env` and injected into the `memorist-core` container.
3. **Skip for now** — configure later in the UI.

### How this bridges the PR5-C security boundary

PR5-C intentionally keeps the **browser** free of plaintext secrets: the Memory
Setup UI stores only an **environment-variable reference** (a name), never a key.
That leaves a gap for a normal local user — someone has to put the key into the
backend environment. The installer fills exactly that gap:

- you type the key **once**, in the terminal, into `Install-Memorist.ps1`;
- it lands only in the local, git-ignored `.env`, restricted to your user;
- Compose passes it into `memorist-core` as a named variable;
- in **Settings → Memorist → Memory Setup** you reference the **name**
  (`MEMORIST_MEMORY_EXTRACTION_API_KEY`), not the value.

The key is never echoed back, never logged (only a `****last4` mask is shown),
never stored in SQLite/PostgreSQL, and never returned by any API. Role variables:

| Role | Variable |
| --- | --- |
| Memory extraction (primary) | `MEMORIST_MEMORY_EXTRACTION_API_KEY` |
| High-confidence extraction | `MEMORIST_HIGH_CONFIDENCE_EXTRACTION_API_KEY` |
| Embedding | `MEMORIST_EMBEDDING_API_KEY` |
| Privacy / sensitivity | `MEMORIST_PRIVACY_SENSITIVITY_API_KEY` |
| Import reconstruction | `MEMORIST_IMPORT_RECONSTRUCTION_API_KEY` |

If you reuse one key across roles, the wizard writes the **same value into each
role variable** so you can pick per-role names in the UI. Remote endpoints still
require the in-app **privacy acknowledgement** before they can become a role
default — the installer does not bypass that consent step.

> **Privacy:** a remote provider means captured memory text may leave your
> machine. Local deterministic mode keeps everything on-device.

## Lite vs Full

| | Lite (default) | Full (advanced preview) |
| --- | --- | --- |
| Store | SQLite | SQLite + FalkorDB graph |
| Services | `memorist-core`, `open-webui` | adds `falkordb` |
| Resource use | Low | Higher |
| One-click reliable | ✅ | ⚠️ preview — start with Lite |

Start Full with `.\Install-Memorist.ps1 -Mode full` or
`.\Start-Memorist.ps1 -Mode full`.

## Everyday commands

| Task | Command |
| --- | --- |
| Start | `.\Start-Memorist.ps1` |
| Stop (keeps data) | `.\Stop-Memorist.ps1` |
| Restart | `.\Restart-Memorist.ps1` |
| Logs | `.\Show-Memorist-Logs.ps1` |
| Reset data (destructive) | `.\Reset-Memorist-Data.ps1` |
| Uninstall | `.\Uninstall-Memorist.ps1` |

`Reset` requires typing `DELETE`; `Uninstall` preserves data volumes unless you
pass `-PurgeData`.

## Where things live

- **Config + secrets:** `.env` (git-ignored; ACL-restricted to your user).
- **Memory data:** Docker named volumes plus `data/ objects/ imports/ exports/`.
- **Logs:** `Show-Memorist-Logs.ps1` or `docker compose logs`.

## Backup & upgrade

Your data is in Docker volumes/local folders, so replacing package files does not
delete memories. To upgrade: `Stop-Memorist.ps1` → swap in the new package files
(keep `.env` and `data/`) → `Start-Memorist.ps1`. Back up with
`scripts/backup.sh` (or `docker compose exec memorist-core python -m
memcore.reliability backup`). See [`upgrade`](../release/memorist-openwebui/docs/upgrade.md)
and [`backup-restore`](backup-restore.md).

## Cross-platform note

The same scripts run on **PowerShell 7** on macOS/Linux
(`pwsh ./Install-Memorist.ps1`). Only the `Memorist.cmd` double-click launcher is
Windows-specific. There is no signed native `.msi` installer in this release.

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| "Docker CLI not found" | Install Docker Desktop; reopen the terminal |
| "daemon is not reachable" | Start Docker Desktop; wait for **Running** |
| Port in use | Edit `OPEN_WEBUI_PORT` / `MEMORIST_PORT` in `.env` |
| UI slow first load | Images still building/pulling — watch the logs |
| "Secret environment variable is not set" | Add the named var to `.env`, then `Restart-Memorist.ps1` |

More: [`troubleshooting`](troubleshooting.md),
[`local-release`](local-release.md), and the packaged `README-LOCAL.md`.
