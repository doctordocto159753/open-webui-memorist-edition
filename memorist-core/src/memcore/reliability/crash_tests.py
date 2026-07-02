from __future__ import annotations

import sqlite3
from typing import Any

from memcore.reliability.consistency import run_consistency_check


def simulate_crash_recovery(connection: sqlite3.Connection) -> dict[str, Any]:
    report = run_consistency_check(connection)
    return {"status": "checked", "consistency": report, "retry_safe": True}
