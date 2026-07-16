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
