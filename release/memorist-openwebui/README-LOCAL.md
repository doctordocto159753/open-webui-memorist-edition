# Memorist — Windows Local Quick Start

Memorist is a local-first memory edition of Open WebUI. This package runs
everything on your own machine with Docker; you do **not** need Git, Python, or
`uv` to use it.

> Status: `0.2.0-beta.3` beta development candidate, storage schema `25`.
> Lite uses SQLite. Full uses PostgreSQL + FalkorDB. Hosted Consolidated CI
> validates both runtime paths; native Windows desktop validation remains a
> separate release gate.

## 1. Requirements

- **Windows 10/11** with **Docker Desktop** installed and running.
  Download: <https://www.docker.com/products/docker-desktop/>
- Lite: ~4 GB free disk. Full: ~8 GB free disk and 6 GB RAM recommended.

That's it. Docker Desktop provides the container engine; the installer detects
it and tells you exactly what to do if it is missing or not started.

## 2. Install (one click)

1. Unzip this package to a folder you own (e.g. `Documents\Memorist`).
2. Double-click **`Memorist.cmd`**.
   - It launches the setup wizard through PowerShell (no system-wide policy
     changes).
3. Follow the short wizard:
   - asks explicitly for Lite (SQLite) or Full (PostgreSQL + FalkorDB);
   - checks Docker, ports, and disk;
   - skips active and Windows-excluded host ports and stores the selected ports;
   - creates local data folders;
   - generates a private `.env` with strong session secrets;
   - asks how memory extraction should run (see below);
   - starts the services and opens your browser to
     <http://localhost:3000>.

Prefer the command line? From this folder:

```powershell
.\Install-Memorist.ps1                            # interactive Lite/Full choice
.\Install-Memorist.ps1 -Mode full -NonInteractive -NoBrowser
.\Install-Memorist.ps1 -Mode lite -DryRun -NonInteractive
.\Install-Memorist.ps1 -Mode lite -EnableOllama            # opt-in only
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
`MEMORIST_MEMORY_EXTRACTION_API_KEY`, under **Settings → Memorist → Processing
Nodes**. Endpoint, model, capabilities, privacy acknowledgement, testing, and
role-default assignment happen there; the installer does not bypass them.
The backend requires a persisted current certification, so refresh does not
lose test authority. The UI distinguishes a configured secret reference from
availability inside Core and last validated authentication.

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
- **Lite canonical memory:** SQLite in the project-scoped `memorist-data` volume.
- **Full canonical memory:** PostgreSQL in `memorist-postgres-data`; FalkorDB
  in `falkordb-data` is a rebuildable projection only.
- **Other data:** stable `memorist-*` volumes plus local `data/`, `objects/`,
  `imports/`, and `exports/` folders.
- **Logs:** `Show-Memorist-Logs.ps1`, or the `logs/` folder.

## 6. Upgrading

Data uses stable Docker volume names independent of the extraction path. To
upgrade: stop Memorist, copy the existing `.env` into the new extracted
package, and rerun the installer in the persisted mode. A Lite-to-Full change
requires the documented SQLite-to-PostgreSQL migration; the installer refuses
to abandon SQLite data silently.
See the packaged `docs/upgrade.md` for upgrade notes and use Heritage export for
a portable backup before alpha-version migrations.

For Full upgrades the installer reuses the stable installation identity and
credentials, then authenticates to PostgreSQL over TCP before Core starts. It
never changes the database role and never removes volumes. If an old `.env`
cannot be recovered, stop and restore it instead of generating a replacement.

## 7. Troubleshooting

- **"Docker CLI not found" / "daemon not reachable":** install/start Docker
  Desktop and wait for **Running**, then re-run.
- **Port unavailable/reserved:** rerun the installer or edit
  `OPEN_WEBUI_PORT` / `MEMORIST_CORE_HOST_PORT` in `.env`. The internal Core
  URL always remains `http://memorist-core:8777`.
- **Chat works but memory is degraded:** open Settings → Memorist →
  Diagnostics. Fail-open keeps chat available but records sanitized per-stage
  failure/recovery state.
- **UI slow to load first time:** images are still pulling/building; watch
  `Show-Memorist-Logs.ps1`.

More detail: the packaged `docs/troubleshooting.md`.
