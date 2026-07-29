# Troubleshooting

Common failures, in the order people usually hit them.

## Install and startup

| Symptom | Fix |
| --- | --- |
| "Docker CLI not found" | Install Docker Desktop, then reopen the terminal and re-run the installer. |
| "Docker is installed but the daemon is not reachable" | Start Docker Desktop and wait until it reports **Running**, then re-run. |
| Docker Desktop won't start on Windows | Usually WSL2: run `wsl --update` in an elevated prompt, ensure virtualization is enabled in BIOS/UEFI, then restart Docker Desktop. |
| Docker reports forbidden socket access although no listener owns 8777 | Windows may reserve an excluded Hyper-V/WSL/Docker range. Rerun the installer; it checks numeric IPv4/IPv6 excluded ranges and persists a safe `MEMORIST_CORE_HOST_PORT`. |
| Selected host port became occupied before startup | Edit `OPEN_WEBUI_PORT` / `MEMORIST_CORE_HOST_PORT` in `.env`, rerun the installer, and let final Compose validation/startup retry. Container port 8777 and `http://memorist-core:8777` do not change. |
| PostgreSQL is healthy but Core reports password authentication failed | Restore the previous `.env` and rerun. The installer tests the password over TCP before starting Core. It never alters the role or deletes the volume. If only an orphaned volume remains, recover the authoritative `.env`/backup. |
| Compose fails to start services | `Show-Memorist-Logs.ps1` (or `scripts/logs.sh`) and look at the first failing service; low disk is a common cause (images need several GB). |
| First launch is very slow | Images are still pulling/building. Watch the logs; subsequent starts are fast. |

## Open WebUI opens but memory is missing

| Symptom | Fix |
| --- | --- |
| No Memorist features in the UI | Confirm the integration is mounted (the release compose does this) and `memorist-core` is healthy: `http://localhost:8777/memcore/health`. |
| `memorist-core` unreachable | `Show-Memorist-Logs.ps1 memorist-core`; check that `MEMORIST_ACTOR_ASSERTION_SECRET`/`MEMORIST_ACTOR_SERVICE_TOKEN` are set in `.env` (the installer generates them). |
| Chat works but never attaches memory | That's the fail-open design when memory is degraded. Check `http://localhost:8777/memcore/diagnostics/daily`, then preflight settings (`MEMORIST_PREFLIGHT_ENABLED`, `MEMORIST_FAIL_OPEN`). |
| A message was captured but created no memory | Open `/memcore/memory-processing/runs/<run-uuid>/stages`; `no_memory_reason` distinguishes no eligible signal, review, rejection, and consolidation with no result. |
| Semantic coverage says `unresolved_reference` | The model did not supply one evidence-bound resolution inside the current two/six-unit same-session manifest. Memorist does not guess a referent. |
| Semantic coverage says `needs_review` | Check gate/route lineage, assistant ratification, privacy ceiling, and accepted evidence. Assistant context and ambiguous acknowledgements cannot become user authority automatically. |
| Restart reports a semantic replay identity conflict | The immutable message version, contract/policy versions, or current gate/route/privacy authority differs from the recorded plan. Preserve the audit rows and inspect the processing trace; do not delete links or edit UUIDs. |

## Provider / API key setup

| Symptom | Fix |
| --- | --- |
| "Secret environment variable is not set" | Add the named variable (e.g. `MEMORIST_MEMORY_EXTRACTION_API_KEY`) to `.env`, then `Restart-Memorist.ps1`. The Memory Setup UI stores the **name**, not the value. |
| "Model not found" on Test | Use the provider's exact model ID. |
| "JSON response format rejected" | Disable the JSON/structured-output capability flags or pick a model that supports them. |
| "Privacy acknowledgement required" | Open the processing node, review the remote data disclosure, and acknowledge it before assigning the profile as a role default. |
| Connection refused/timeout on Test | The base URL must be reachable **from the memorist-core container**, not just from your browser. For a service on the host, use `host.docker.internal` instead of `localhost`. |
| No API key at all | That's fine — local deterministic mode runs the whole memory pipeline without any remote provider. |
| Profile is saved but a fallback is effective | Open Processing Nodes or `GET /memcore/model-control/effective`; inspect `scope_source`, `inheritance_source`, `fallback_reason`, and certification status. Endpoint/model/capability/secret-reference edits require a new test. |
| 401/403 on Test | The endpoint is reachable; verify the secret env-var name/value and provider permissions. |
| 429 on Test | The endpoint is reachable but rate limited. Wait/retry or check quota; this is not an authentication or connection failure. |
| Wrong embedding dimension | Set the profile's real vector dimension, retest, and rebuild stale embeddings. |
| Valid JSON but marker mismatch | The endpoint/auth/model worked, but the role contract did not. Memorist performs one corrective retry; if it still fails, use a model that follows the exact marker/schema. |
| Test lasts longer than preflight | Expected: provider tests have their own timeout. A proxy timeout is reported as 504, not a generic Core 500. |
| Repeated Ollama connection errors with no Ollama installed | Keep `ENABLE_OLLAMA_API=false` (the package default). Enable it only deliberately; OpenAI-compatible nodes are configured separately. |
| First boot downloads a local sentence-transformer | Keep `OPENWEBUI_RAG_EMBEDDING_ENGINE=openai` (the package default). Set a local engine only after explicitly provisioning it. |

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

- Graph projection is optional only in Lite. Full requires FalkorDB and refuses
  to start when its PostgreSQL/FalkorDB/runtime contract is inconsistent.
- Full mode issues usually mean PostgreSQL or FalkorDB didn't come up — check
  their logs and the healthchecks in the compose output. Full remains an
  see [INSTALLATION.md](INSTALLATION.md#lite-vs-full).

## Logs

- Live logs: `Show-Memorist-Logs.ps1 [service]` or `scripts/logs.sh`.
- Diagnostics endpoints: `/memcore/diagnostics/daily`,
  `/memcore/diagnostics/write-actor`,
  `/memcore/model-control/effective`, and
  `/memcore/memory-processing/runs/<run-uuid>/stages`.

Still stuck? Open an issue with the installer/doctor output and the first
error from the logs — with any API keys redacted.
# Inspecting provider contract failures

Open `/memcore/memory-processing/runs/{processing_run_uuid}/stages`. The trace
contains final canonical stages and `provider_attempts`. Check
`transport_status`, `parse_status`, `schema_valid`, `canonicalized`,
`attempt_kind`, and `validation_errors_*`. An `unknown_completion` reservation
means the worker cannot prove whether a paid call completed and deliberately did
not call the provider again. `failed_open` means deterministic output kept the
pipeline available; it does not mean the provider succeeded.

Certification is role-contract-specific. A profile that returns HTTP 200 and
passes the generic connectivity marker can still be rejected if it fails the
active role prompt/schema probe. Retest after changing model, endpoint,
capabilities, prompt contract, or role manifest; any such change makes the prior
certification stale.

For WP02, `memory_extraction` must pass both Jakobson v3 and semantic candidate
v1 with the same profile. Coverage audit is intentionally content-free; use
canonical message/evidence access controls for raw text rather than expecting
it in generic diagnostics.
