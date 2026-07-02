from __future__ import annotations

import sqlite3


def vacuum(connection: sqlite3.Connection) -> dict[str, str]:
    connection.execute("VACUUM")
    return {"status": "ok", "operation": "vacuum"}


def wal_checkpoint(connection: sqlite3.Connection) -> dict[str, object]:
    rows = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
    return {"status": "ok", "operation": "wal-checkpoint", "result": [tuple(row) for row in rows]}


def secure_delete_check(connection: sqlite3.Connection) -> dict[str, object]:
    row = connection.execute("PRAGMA secure_delete").fetchone()
    enabled = bool(row[0]) if row is not None else False
    tradeoff = (
        "secure_delete improves deletion assurance but can reduce write/delete performance; "
        "filesystem and SSD behavior still limit physical deletion claims."
    )
    return {"enabled": enabled, "tradeoff": tradeoff}
