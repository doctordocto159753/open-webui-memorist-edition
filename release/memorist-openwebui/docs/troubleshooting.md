# Troubleshooting

Run:

```bash
scripts/doctor.sh lite
scripts/doctor.sh full
scripts/logs.sh
```

If Memorist Core is disconnected, Open WebUI should still start and chat should fail open without memory attachment.

Installation verification does not fail open. If Full reports a Lite profile,
SQLite canonical store, disabled graph/worker/attachment/import/forget feature,
or unhealthy PostgreSQL/FalkorDB, the installer exits non-zero and does not
print a success message.

The installer separates Windows host ports from container networking. It skips
active listeners and numeric IPv4/IPv6 excluded ranges; Open WebUI always uses
`http://memorist-core:8777` internally. If PostgreSQL credentials do not match a
preserved volume, restore the prior `.env`; the installer never alters the role
or deletes data.

Provider tests use a longer operation-specific timeout than interactive
preflight. Fail-open chat errors are written as sanitized per-stage outcomes
and appear under Settings → Memorist → Diagnostics until that stage recovers.
Ollama discovery is disabled unless explicitly enabled.
`OPENWEBUI_RAG_EMBEDDING_ENGINE=openai` also prevents Open WebUI from treating
an empty engine as a request to download a local sentence-transformer.
