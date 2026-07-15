# Local Release Packaging & Validation

This document describes how the Memorist local release package is built,
validated, and shipped. It is aimed at maintainers; end users should read
[`windows-local-install.md`](windows-local-install.md).

## What ships

The user-facing artifact is the package under `release/memorist-openwebui/`,
zipped as `memorist-openwebui-<version>.zip`. It is self-contained: a user
unzips it and runs the installer without a source checkout, Python, or `uv`.

Key files:

| File | Purpose |
| --- | --- |
| `Memorist.cmd` | Windows double-click launcher → PowerShell installer |
| `Install-Memorist.ps1` | Setup wizard (detect → configure → start → open) |
| `Start/Stop/Restart-Memorist.ps1` | Lifecycle control |
| `Reset-Memorist-Data.ps1` / `Uninstall-Memorist.ps1` | Destructive helpers (guarded) |
| `Show-Memorist-Logs.ps1` | Follow container logs |
| `scripts/MemoristCommon.psm1` | Shared PowerShell module (detection, secrets, health) |
| `scripts/*.sh` | Bash equivalents (doctor, start-lite/full, backup, restore) |
| `compose.yml` | Release orchestration with `lite`/`full` profiles + healthchecks |
| `.env.example` | Config template copied to `.env` on install |
| `checksums.sha256` | Integrity manifest (`sha256sum -c`) |

A repo-root [`docker-compose.release.yml`](../docker-compose.release.yml) mirrors
the packaged `compose.yml` with source-checkout-relative paths. It is the
canonical file CI validates and is convenient when running from a clone.

## Compose contract

- Profiles: `lite` (core + UI, SQLite), `full` (adds FalkorDB graph).
- Secrets/keys come from `.env`; none appear on the command line.
- `memorist-core` receives the PR5-C API-key role variables so a locally-entered
  key is resolvable by the backend (empty by default → local deterministic).
- Healthchecks gate the installer's "OK" progress output.
- Only loopback/necessary ports are published.

## Secret & privacy model

- Plaintext keys never enter the browser or database (PR5-C boundary preserved).
- The installer writes keys **only** to the local `.env`, tightens its ACL, and
  masks any display to `****last4`.
- The static validator forbids `Write-Host`/`Write-Output` of `$plain`/`$secure`.
- `.env` and `.env.*` (except `.env.example`) are git-ignored and
  release-ignored.

## Building the package

```bash
python installer/scripts/assemble_rc.py     # builds release/rc/memorist-openwebui-<version>.zip
# or the guarded wrapper that also scans for forbidden files:
python release/build_release.py
```

Refresh the package integrity manifest after editing installer/compose files:

```bash
python release/memorist-openwebui/scripts/gen_checksums.py         # rewrite
python release/memorist-openwebui/scripts/gen_checksums.py --check # verify (CI)
```

## Validation & CI

Workflow: [`.github/workflows/pr5d-one-click-installer.yml`](../.github/workflows/pr5d-one-click-installer.yml).

| Job | What it does |
| --- | --- |
| Static Validation | `installer/scripts/validate_installer.py` (files, functions, secret redaction, env vars, compose bridge, dry-run guards). Runs a PowerShell `Parser::ParseFile` syntax check and Pester tests **when `pwsh` is present**. |
| Compose Config | `docker compose -f … config -q` for release + package, `lite` + `full`; asserts the API-key passthrough renders. |
| Dry Run | `Install-Memorist.ps1 -DryRun -NonInteractive` when `pwsh` exists (asserts no `.env` written); otherwise the static dry-run guards. |
| Release Manifest | `gen_checksums.py --check` — checksums cover the installer entry points. |

### PowerShell on Linux CI

The self-hosted `memorist-ci` runners are Linux. If `pwsh` is installed there,
the parse/Pester/dry-run steps execute a real PowerShell; if not, they degrade to
the Python static validator and print a clear note. **Full Windows end-to-end
(double-click → Docker up → chat) is not run in CI** and is verified manually.
This limitation is stated honestly rather than claimed as covered.

## Manual verification checklist (Windows)

1. Unzip package; double-click `Memorist.cmd`.
2. Confirm Docker/port/disk checks and the wizard prompts.
3. Choose local deterministic → services start → browser opens to :3000.
4. Chat; confirm capture, Memory On/Off, and Memory used attachment work.
5. Re-run with a remote key; confirm the key is masked and referenced by name in
   Memory Setup.
6. `Stop` → `Start` (data persists); `Reset-Memorist-Data` (guarded) clears it.
