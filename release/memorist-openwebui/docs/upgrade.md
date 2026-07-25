# Upgrade

Before upgrade:

1. Run `scripts/backup.sh`.
2. Stop services.
3. Extract the new package and copy the old `.env` into it.
4. Rerun the installer without changing the persisted mode. For Full, the
   installer starts PostgreSQL alone and verifies the preserved password over
   TCP before starting Core; it never runs `ALTER ROLE`.
5. Start and run `scripts/doctor.sh lite|full` for that mode.
6. Verify `/memcore/version` schema version.

Rollback is by restoring a backup; migration rollback is not implemented.
Stable project and volume names retain PostgreSQL, SQLite, FalkorDB, and Open
WebUI accounts across extraction-path changes. Lite-to-Full is not an ordinary
upgrade: use the certified SQLite-to-PostgreSQL migration first.

If the previous containers still exist but `.env` was not copied, the installer
can recover allow-listed identity and credential values without printing them.
If only an orphaned PostgreSQL volume remains, restore the authoritative
previous `.env` or backup. No automatic destructive reset is attempted.
