from __future__ import annotations

import sqlite3
from typing import Any

from memcore.models import utc_now


def ensure_progress(
    connection: sqlite3.Connection,
    import_run_uuid: str,
    phase: str = "idle",
    records_total: int = 0,
) -> dict[str, Any]:
    with connection:
        connection.execute(
            """
            INSERT OR IGNORE INTO import_progress (
                import_run_uuid, phase, records_total, updated_at
            )
            VALUES (?, ?, ?, ?)
            """,
            (import_run_uuid, phase, records_total, utc_now()),
        )
    return get_progress(connection, import_run_uuid)


def update_progress(
    connection: sqlite3.Connection,
    import_run_uuid: str,
    **values: Any,
) -> dict[str, Any]:
    if (
        connection.execute(
            "SELECT 1 FROM import_progress WHERE import_run_uuid = ?",
            (import_run_uuid,),
        ).fetchone()
        is None
    ):
        ensure_progress(connection, import_run_uuid)
    if not values:
        return get_progress(connection, import_run_uuid)
    values["updated_at"] = utc_now()
    assignments = ", ".join(f"{key} = ?" for key in values)
    with connection:
        connection.execute(
            f"UPDATE import_progress SET {assignments} WHERE import_run_uuid = ?",
            tuple(values.values()) + (import_run_uuid,),
        )
    return get_progress(connection, import_run_uuid)


def get_progress(connection: sqlite3.Connection, import_run_uuid: str) -> dict[str, Any]:
    row = connection.execute(
        "SELECT * FROM import_progress WHERE import_run_uuid = ?",
        (import_run_uuid,),
    ).fetchone()
    if row is None:
        return ensure_progress(connection, import_run_uuid)
    result = dict(row)
    result["throttled"] = bool(result["throttled"])
    result["paused"] = bool(result["paused"])
    result["cancelled"] = bool(result["cancelled"])
    result["estimated_remaining"] = None
    return result
