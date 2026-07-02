from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from memcore.heritage.package import restore_heritage_into_connection
from memcore.storage.commands import WriteResult
from memcore.storage.write_actor import get_write_actor


@dataclass(frozen=True)
class HeritageRestoreCommand:
    package_path: str
    dry_run: bool = False
    command_type: str = "heritage.restore"
    priority: int = 25
    estimated_write_weight: int = 10_000
    max_transaction_ms: int = 60_000
    can_batch: bool = False

    @property
    def idempotency_key(self) -> str | None:
        return None if not self.dry_run else f"heritage:dry-run:{self.package_path}"

    def execute(self, connection: sqlite3.Connection) -> WriteResult:
        result = restore_heritage_into_connection(
            self.package_path,
            connection,
            dry_run=self.dry_run,
        )
        return WriteResult(
            command_type=self.command_type,
            target_type="heritage_package",
            target_uuid=None,
            result=result,
        )


def restore_heritage_via_actor(
    package_path: str | Path,
    db_path: str | Path,
    dry_run: bool = False,
    timeout: float | None = 180,
) -> dict[str, Any]:
    result = get_write_actor(db_path).submit_sync(
        HeritageRestoreCommand(str(package_path), dry_run=dry_run),
        timeout=timeout,
    )
    return result.result

