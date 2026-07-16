# Troubleshooting

Common failures, in the order people usually hit them.

## Install and startup

| Symptom | Fix |
| --- | --- |
| "Docker CLI not found" | Install Docker Desktop, then reopen the terminal and re-run the installer. |
| "Docker is installed but the daemon is not reachable" | Start Docker Desktop and wait until it reports **Running**, then re-run. |
| Docker Desktop won't start on Windows | Usually WSL2: run `wsl --update` in an elevated prompt, ensure virtualization is enabled in BIOS/UEFI, then restart Docker Desktop. |
| "Port 3000/8777 is in use" | Edit `OPEN_WEBUI_PORT` / `MEMORIST_PORT` in `.env`, or stop the other app, then `Restart-Memorist.ps1`. |
| Compose fails to start services | `Show-Memorist-Logs.ps1` (or `scripts/logs.sh`) and look at the first failing service; low disk is a common cause (images need several GB). |
| First launch is very slow | Images are still pulling/building. Watch the logs; subsequent starts are fast. |

## Open WebUI opens but memory is missing

| Symptom | Fix |
| --- | --- |
| No Memorist features in the UI | Confirm the integration is mounted (the release compose does this) and `memorist-core` is healthy: `http://localhost:8777/memcore/health`. |
| `memorist-core` unreachable | `Show-Memorist-Logs.ps1 memorist-core`; check that `MEMORIST_ACTOR_ASSERTION_SECRET`/`MEMORIST_ACTOR_SERVICE_TOKEN` are set in `.env` (the installer generates them). |
| Chat works but never attaches memory | That's the fail-open design when memory is degraded. Check `http://localhost:8777/memcore/diagnostics/daily`, then preflight settings (`MEMORIST_PREFLIGHT_ENABLED`, `MEMORIST_FAIL_OPEN`). |

## Provider / API key setup

| Symptom | Fix |
| --- | --- |
| "Secret environment variable is not set" | Add the named variable (e.g. `MEMORIST_MEMORY_EXTRACTION_API_KEY`) to `.env`, then `Restart-Memorist.ps1`. The Memory Setup UI stores the **name**, not the value. |
| "Model not found" on Test | Use the provider's exact model ID. |
| "JSON response format rejected" | Disable the JSON/structured-output capability flags or pick a model that supports them. |
| "Privacy acknowledgement required" | Open the processing node, review the remote data disclosure, and acknowledge it before assigning the profile as a role default. |
| Connection refused/timeout on Test | The base URL must be reachable **from the memorist-core container**, not just from your browser. For a service on the host, use `host.docker.internal` instead of `localhost`. |
| No API key at all | That's fine — local deterministic mode runs the whole memory pipeline without any remote provider. |

## Memory behavior questions

| Symptom | Explanation |
| --- | --- |
| Turn shows no "Memory used" panel | Nothing was attached that turn — retrieval abstained, memory was off, or no relevant memory exists. The panel only appears when memory was actually attached. |
| Memory Off but old memories still exist | Memory Off stops **new** capture, retrieval, and attachment for that chat; it does not delete existing memories. Use the privacy/forget workflow to erase. |
| Regenerated response ignored the toggle | Regeneration uses the memory state recorded on the **original** turn, by design. |
| Greeting/small talk created no memory | Correct — phatic-only turns are gated out before candidate creation. |

## Data, reset, and recovery

| Task | Command |
| --- | --- |
| Full health check | `scripts/doctor.sh lite` (bash) — checks Docker, folders, ports, endpoints |
| Reliability check | `cd memorist-core && uv run python -m memcore.reliability check` |
| SQLite file keeps growing | `uv run python -m memcore.reliability wal-checkpoint` (outside hot paths); avoid `VACUUM` during active use |
| Backup | `scripts/backup.sh` — uses the SQLite backup API; never copy a live WAL database by hand |
| Restore | `scripts/restore.sh path/to/heritage.zip` (dry-runs first) |
| Reset everything | `Reset-Memorist-Data.ps1` — asks for `DELETE` confirmation; destructive |

## Import problems

- Unsafe ZIP paths, nested archives, oversized archives, and suspicious
  compression ratios are rejected before extraction — that's the staging
  security check, not a corruption bug.
- Heavy imports intentionally run at lower priority; if live chat feels slow,
  check `/memcore/diagnostics/daily` and pause the import.

## Graph / Full mode

- Graph projection is optional. When in doubt, use Lite or set
  `MEMORIST_GRAPH_BACKEND=disabled`.
- Full mode issues usually mean PostgreSQL or FalkorDB didn't come up — check
  their logs and the healthchecks in the compose output. Full remains an
  advanced preview; see [INSTALLATION.md](INSTALLATION.md#lite-vs-full).

## Logs

- Live logs: `Show-Memorist-Logs.ps1 [service]` or `scripts/logs.sh`.
- Diagnostics endpoints: `/memcore/diagnostics/daily`,
  `/memcore/diagnostics/write-actor`.

Still stuck? Open an issue with the installer/doctor output and the first
error from the logs — with any API keys redacted.
