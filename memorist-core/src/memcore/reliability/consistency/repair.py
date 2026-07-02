from __future__ import annotations

import sqlite3
from typing import Any

from memcore.reliability.consistency.checks import table_exists


def safe_repair(connection: sqlite3.Connection) -> dict[str, Any]:
    repaired: list[str] = []
    if table_exists(connection, "jobs"):
        with connection:
            connection.execute("UPDATE jobs SET status = 'dead' WHERE status IN ('claimed')")
        repaired.append("orphaned_claimed_jobs_marked_dead")
    if table_exists(connection, "memory_blocks"):
        with connection:
            connection.execute(
                "UPDATE memory_blocks SET status = 'stale' WHERE status = 'building'"
            )
        repaired.append("building_blocks_marked_stale")
    if table_exists(connection, "memory_version_fts"):
        with connection:
            connection.execute(
                "INSERT INTO memory_version_fts(memory_version_fts) VALUES('rebuild')"
            )
        repaired.append("memory_version_fts_rebuilt")
    return {"repaired": repaired, "fabricated_missing_content": False}
