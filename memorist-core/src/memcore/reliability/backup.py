from __future__ import annotations

import sqlite3
from pathlib import Path


def backup_sqlite(source: sqlite3.Connection, output_path: str | Path) -> dict[str, object]:
    target_path = Path(output_path)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target = sqlite3.connect(target_path)
    try:
        source.backup(target)
    finally:
        target.close()
    return {"status": "ok", "backup_path": str(target_path)}
