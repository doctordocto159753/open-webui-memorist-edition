# Upgrade

Before upgrade:

1. Run `scripts/backup.sh`.
2. Stop services.
3. Extract the new package and copy the old `.env` into it.
4. Rerun the installer without changing the persisted mode.
5. Start and run `scripts/doctor.sh lite|full` for that mode.
6. Verify `/memcore/version` schema version.

Rollback is by restoring a backup; migration rollback is not implemented.
Stable project and volume names retain PostgreSQL, SQLite, FalkorDB, and Open
WebUI accounts across extraction-path changes. Lite-to-Full is not an ordinary
upgrade: use the certified SQLite-to-PostgreSQL migration first.
