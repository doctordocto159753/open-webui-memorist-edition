# Installation

This guide covers running Memorist locally: the Windows-first one-click
release path, the cross-platform script path, and first-run configuration.
Developer setup from source lives in [DEVELOPMENT.md](DEVELOPMENT.md).

> **Status:** early public alpha. **Lite** uses SQLite. **Full** uses PostgreSQL
> + FalkorDB and has passed all 11 backend/runtime gates in the tested Linux
> Docker environment. Real Windows desktop one-click E2E remains pending.

## Requirements

| Need | Detail |
| --- | --- |
| OS | Windows 10/11 first-class; macOS/Linux via PowerShell 7 or bash scripts |
| Container engine | **Docker Desktop** on Windows, or Docker Engine + Compose on Linux |
| Disk | Lite: ~4 GB; Full: ~8 GB and 6 GB RAM recommended |
| Network | Required to pull images and, optionally, reach a remote model provider |

Docker Desktop is currently required for the Windows one-click path. The
installer detects a missing CLI, stopped daemon, or missing Compose and exits
with remediation instructions. A Dockerless build is not part of this release.

## Release package layout

Download and extract `memorist-openwebui-<version>.zip` into a folder you own:

```text
memorist-openwebui/
  Memorist.cmd
  Install-Memorist.ps1
  Start-Memorist.ps1 / Stop-Memorist.ps1 / Restart-Memorist.ps1
  Show-Memorist-Logs.ps1
  Reset-Memorist-Data.ps1 / Uninstall-Memorist.ps1
  compose.yml
  compose.lite.yml / compose.full.yml
  runtime/
  .env.example
  README-LOCAL.md
  scripts/
  docs/
  checksums.sha256
```

The packaged Docker build contexts are under `runtime/`; installation must not
need Git, Python, `uv`, or a repository checkout.

## Install on Windows

1. Start Docker Desktop and wait until it reports **Running**.
2. Double-click **`Memorist.cmd`**.
3. Choose Lite or Full explicitly.
4. The wizard checks Docker, ports (`3000`, `8777`), and disk; generates a local
   `.env` with strong secrets; optionally stores role-key values; validates the
   effective Compose configuration; starts services; waits for health; verifies
   the requested runtime; and opens <http://localhost:3000>.

From PowerShell:

```powershell
.\Install-Memorist.ps1
.\Install-Memorist.ps1 -Mode full -NonInteractive -NoBrowser
.\Install-Memorist.ps1 -Mode lite -DryRun -NonInteractive
```

A dry run writes no `.env` and starts no containers. It returns non-zero when
Docker/Compose is unavailable or the effective Compose configuration is
invalid; it must not print a successful dry-run after failed validation.

Re-running the installer preserves existing generated secrets and the installed
mode. It refuses an implicit Lite/Full switch. A Lite-to-Full move requires the
certified migration/export path rather than silently abandoning SQLite data.

## Memory processing and API keys

The wizard offers:

| Option | Needs API key? | Data boundary |
| --- | --- | --- |
| Local deterministic | No | Conversation-derived processing stays local |
| Store optional provider key locally | Yes | Role-specific data may later be sent to the configured provider |
| Skip for now | No | Configure later |

The installer stores optional key values only in the local, git-ignored,
ACL-restricted `.env` and injects them into `memorist-core`. The browser and
databases store only an environment-variable reference, never the plaintext
value.

Role variables:

```text
MEMORIST_MEMORY_EXTRACTION_API_KEY
MEMORIST_HIGH_CONFIDENCE_EXTRACTION_API_KEY
MEMORIST_EMBEDDING_API_KEY
MEMORIST_PRIVACY_SENSITIVITY_API_KEY
MEMORIST_IMPORT_RECONSTRUCTION_API_KEY
```

Endpoint, model, capability flags, privacy acknowledgement, profile testing,
and role-default assignment happen in:

```text
Settings → Memorist → Processing Nodes
```

The installer does not bypass those admin and privacy controls.

## First run

1. Create or sign in to your Open WebUI account; the first account is admin.
2. Open **Settings → Memorist → Processing Nodes**.
3. Keep local deterministic processing or create and test a remote
   OpenAI-compatible profile, acknowledge its privacy boundary, and assign role
   defaults.
4. Chat normally. The **Memory On / Memory Off** switch sits beside the composer;
   turns that used memory show the read-only **Memory used** panel.

Health endpoints:

```text
http://localhost:3000/health
http://localhost:8777/memcore/health
http://localhost:8777/memcore/config/effective
http://localhost:8777/memcore/diagnostics/daily
```

## Lite versus Full

| | Lite | Full |
| --- | --- | --- |
| Canonical store | SQLite | PostgreSQL |
| Graph projection | disabled | FalkorDB |
| Services | `memorist-core`, `open-webui` | + `postgres`, `falkordb` |
| Scheduler | disabled | `in_memory` |
| Required memory features | enabled local memory path | enabled + graph projection |
| Database/graph host ports | none | none |
| Certification | validated local path | backend/runtime 11/11 on tested Linux Docker; Windows E2E pending |

Lite and Full share the same canonical semantic decisions. Full adds the
PostgreSQL canonical ledger, graph projection, and heavier runtime; it is not
Lite plus an unused graph container.

A successful Full installer run verifies live values equivalent to:

```text
runtime_profile=full
canonical_store=postgres
postgres_dsn_configured=true
graph_backend=falkordb
hot_scheduler=in_memory
graph_status=ok
```

## Everyday commands

| Task | Windows | bash |
| --- | --- | --- |
| Start | `.\Start-Memorist.ps1` | `scripts/start-lite.sh` or `scripts/start-full.sh` |
| Stop, preserving data | `.\Stop-Memorist.ps1` | `scripts/stop.sh` |
| Restart | `.\Restart-Memorist.ps1` | stop then selected-mode start |
| Logs | `.\Show-Memorist-Logs.ps1` | `scripts/logs.sh` |
| Health | `.\Test-Memorist-Full.ps1` for Full | `scripts/doctor.sh lite\|full` |
| Reset all data | `.\Reset-Memorist-Data.ps1` | documented destructive reset script |
| Uninstall | `.\Uninstall-Memorist.ps1` | `docker compose down` through packaged helpers |

Lifecycle scripts read `MEMORIST_MODE` from `.env`. Stop, Reset, and Uninstall
return non-zero when Compose operations fail. Reset must not delete local
folders and then claim success while named volumes remain.

Reset requires typing `DELETE`. Uninstall preserves volumes unless
`-PurgeData` is explicitly selected and confirmed.

## Where data lives

- Config and secrets: `.env` in the package folder.
- Lite canonical data: `memorist-data`.
- Full canonical data: `memorist-postgres-data`.
- Full graph projection: `falkordb-data`, rebuildable from PostgreSQL.
- Open WebUI accounts: `openwebui-data`.
- Objects, imports, and exports: project-scoped Memorist volumes/folders.

PostgreSQL and FalkorDB are internal Compose services and do not publish host
ports in the release package.

## Backup and upgrade

Before an alpha-version upgrade, create a Heritage export or the documented
mode-appropriate backup.

Upgrade procedure:

1. stop Memorist;
2. extract the new package;
3. copy the existing `.env` into it;
4. rerun the installer without changing the persisted mode;
5. verify health and effective runtime.

Stable project and volume names retain data across extraction-path changes.
Schema migration rollback is not automatic.

## Known limitations

- Docker Desktop or Docker Engine + Compose is required.
- There is no signed native `.msi`/`.exe`; SmartScreen may warn about scripts.
- Full backend/runtime certification is specific to the tested Linux Docker
  environment, not a production-readiness or security-audit claim.
- Real Windows desktop one-click and lifecycle E2E remains pending.
- Remote semantic quality depends on the profiles and models you configure.

For failures, see [TROUBLESHOOTING.md](TROUBLESHOOTING.md).
