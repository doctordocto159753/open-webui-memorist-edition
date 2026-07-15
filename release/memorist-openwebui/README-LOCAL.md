# Memorist — Windows Local Quick Start

Memorist is a local-first memory edition of Open WebUI. This package runs
everything on your own machine with Docker; you do **not** need Git, Python, or
`uv` to use it.

> Status: alpha/beta. Lite mode is the validated one-click path. Full mode
> (PostgreSQL + FalkorDB graph) is an advanced, more resource-hungry preview.

## 1. Requirements

- **Windows 10/11** with **Docker Desktop** installed and running.
  Download: <https://www.docker.com/products/docker-desktop/>
- ~5 GB free disk for images and data.

That's it. Docker Desktop provides the container engine; the installer detects
it and tells you exactly what to do if it is missing or not started.

## 2. Install (one click)

1. Unzip this package to a folder you own (e.g. `Documents\Memorist`).
2. Double-click **`Memorist.cmd`**.
   - It launches the setup wizard through PowerShell (no system-wide policy
     changes).
3. Follow the short wizard:
   - it checks Docker, ports, and disk;
   - creates local data folders;
   - generates a private `.env` with strong session secrets;
   - asks how memory extraction should run (see below);
   - starts the services and opens your browser to
     <http://localhost:3000>.

Prefer the command line? From this folder:

```powershell
.\Install-Memorist.ps1            # Lite (recommended)
.\Install-Memorist.ps1 -Mode full # Advanced
.\Install-Memorist.ps1 -DryRun    # Validate only; writes nothing, starts nothing
```

## 3. Memory processing options

| Option | Needs API key? | Where data goes |
| --- | --- | --- |
| **Local deterministic** (default) | No | Stays fully local |
| **OpenAI-compatible remote** | Yes | Memory text may be sent to the provider |
| **Skip for now** | No | Configure later in the UI |

If you choose a remote provider, the wizard stores your key **only** in the
local `.env` file and injects it into the `memorist-core` container. The key is
**never** shown again, never written to logs, and never stored in the browser or
database. In the app you reference the key by its **variable name**, for example
`MEMORIST_MEMORY_EXTRACTION_API_KEY`, under **Settings → Memorist → Memory
Setup**. This bridges the PR5-C env-var reference model for local users.

## 4. Everyday commands

| Task | Script |
| --- | --- |
| Start | `.\Start-Memorist.ps1` |
| Stop (keeps data) | `.\Stop-Memorist.ps1` |
| Restart | `.\Restart-Memorist.ps1` |
| View logs | `.\Show-Memorist-Logs.ps1` |
| Reset all data | `.\Reset-Memorist-Data.ps1` |
| Uninstall | `.\Uninstall-Memorist.ps1` |

Reset and uninstall warn before deleting anything. Uninstall keeps your memory
volumes by default; pass `-PurgeData` to remove them.

## 5. Where things live

- **Config + secrets:** `.env` in this folder (git-ignored; keep it private).
- **Memory data:** Docker named volumes plus the local `data/`, `objects/`,
  `imports/`, `exports/` folders.
- **Logs:** `Show-Memorist-Logs.ps1`, or the `logs/` folder.

## 6. Upgrading

Your data lives in Docker volumes and local folders, so upgrading images does
not delete memories. To upgrade: stop Memorist, replace the package files with
the new release (keep your `.env` and `data/`), then run `Start-Memorist.ps1`.
See `docs/upgrade.md` and `docs/backup-restore.md` for backups.

## 7. Troubleshooting

- **"Docker CLI not found" / "daemon not reachable":** install/start Docker
  Desktop and wait for **Running**, then re-run.
- **Port already in use:** edit `OPEN_WEBUI_PORT` / `MEMORIST_PORT` in `.env`.
- **UI slow to load first time:** images are still pulling/building; watch
  `Show-Memorist-Logs.ps1`.

More detail: `docs/troubleshooting.md` and the repository
`docs/windows-local-install.md`.
