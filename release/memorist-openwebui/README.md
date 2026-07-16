# Memorist OpenWebUI Local Package

Package version: `0.2.0-beta.1`  
Status: early public alpha.

This self-contained package runs Open WebUI with Memorist Core. Lite uses SQLite.
Full uses PostgreSQL as the only canonical memory store and FalkorDB as a
rebuildable graph projection. The Full backend/runtime has passed the eleven
Docker certification gates on the tested Linux environment. Windows desktop
one-click validation is tracked separately and must not be inferred from that
backend certification.

## Windows one-click

1. Install and start Docker Desktop.
2. Extract the ZIP into a folder you own.
3. Double-click `Memorist.cmd`.
4. Choose Lite or Full in the wizard.

The installer creates an ACL-restricted local `.env`, generates strong session
secrets, generates a private PostgreSQL password for Full, starts the selected
services, verifies the effective runtime, and opens `http://localhost:3000` only
after required checks pass.

Command-line equivalents:

```powershell
.\Install-Memorist.ps1
.\Install-Memorist.ps1 -Mode full -NonInteractive -NoBrowser
.\Install-Memorist.ps1 -Mode lite -DryRun -NonInteractive
```

## Runtime matrix

| | Lite | Full |
| --- | --- | --- |
| Services | Open WebUI, Memorist Core | Open WebUI, Memorist Core, PostgreSQL, FalkorDB |
| Canonical store | SQLite | PostgreSQL |
| Graph | disabled | FalkorDB projection |
| Scheduler | disabled | `in_memory` |
| Database/graph host ports | none | none |

## Processing nodes and API keys

Local deterministic processing needs no API key. The installer may store
optional role-key values only in the local `.env`. Endpoint, model, capability
flags, privacy acknowledgement, profile testing, and role-default assignment
are completed in **Settings → Memorist → Processing Nodes**. Plaintext key
values are not stored in the browser, SQLite, PostgreSQL, or FalkorDB.

## Lifecycle

```powershell
.\Start-Memorist.ps1
.\Stop-Memorist.ps1
.\Restart-Memorist.ps1
.\Show-Memorist-Logs.ps1
.\Reset-Memorist-Data.ps1
.\Uninstall-Memorist.ps1
```

The scripts read the installed mode from `.env`. Uninstall preserves volumes by
default. `-PurgeData` and Reset are destructive and require explicit
confirmation.

## Data

- Lite canonical data: `memorist-data`
- Full canonical data: `memorist-postgres-data`
- Full graph projection: `falkordb-data`
- Open WebUI accounts: `openwebui-data`
- Objects/imports/exports: project-scoped Memorist volumes

See `README-LOCAL.md` and the packaged `docs/` directory for installation,
security, troubleshooting, and upgrade details.
