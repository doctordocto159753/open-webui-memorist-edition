from __future__ import annotations

import sqlite3
from typing import Any


def migration_status(connection: sqlite3.Connection) -> dict[str, Any]:
    rows = connection.execute(
        "SELECT migration_id, checksum, applied_at FROM schema_migrations ORDER BY migration_id"
    ).fetchall()
    warning = "SQLite migration rollback is not implemented; restore from backup instead."
    return {
        "schema_version": len(rows),
        "migrations": [dict(row) for row in rows],
        "rollback_supported": False,
        "rollback_warning": warning,
    }
