import sqlite3


def conflict_penalty(connection: sqlite3.Connection, memory_uuid: str) -> float:
    row = connection.execute(
        """
        SELECT COUNT(*) AS count
        FROM memory_consolidation_decisions
        WHERE operation = 'CONTRADICT' AND target_memory_uuid = ?
        """,
        (memory_uuid,),
    ).fetchone()
    return 0.35 if row is not None and int(row["count"]) > 0 else 0.0
