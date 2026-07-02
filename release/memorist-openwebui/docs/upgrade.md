# Upgrade

Before upgrade:

1. Run `scripts/backup.sh`.
2. Stop services.
3. Replace package files.
4. Start Lite.
5. Run `scripts/doctor.sh lite`.
6. Verify `/memcore/version` schema version.

Rollback is by restoring a backup; migration rollback is not implemented.
