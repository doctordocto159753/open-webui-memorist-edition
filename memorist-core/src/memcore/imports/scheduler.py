from __future__ import annotations

import sqlite3
from typing import Any

from memcore.config import Settings
from memcore.imports.progress import ensure_progress, get_progress, update_progress
from memcore.imports.throttle import should_throttle_import
from memcore.storage.write_actor import get_write_actor


class ImportScheduler:
    def __init__(self, connection: sqlite3.Connection, settings: Settings) -> None:
        self.connection = connection
        self.settings = settings

    def progress(self, import_run_uuid: str) -> dict[str, Any]:
        return ensure_progress(self.connection, import_run_uuid)

    def pause(self, import_run_uuid: str, reason: str = "user_requested") -> dict[str, Any]:
        with self.connection:
            self.connection.execute(
                "UPDATE import_runs SET status = 'paused' WHERE import_run_uuid = ?",
                (import_run_uuid,),
            )
        return update_progress(
            self.connection,
            import_run_uuid,
            paused=1,
            throttled=1 if reason != "user_requested" else 0,
            throttle_reason=reason,
            phase="paused",
        )

    def resume(self, import_run_uuid: str) -> dict[str, Any]:
        with self.connection:
            self.connection.execute(
                """
                UPDATE import_runs
                SET status = CASE WHEN status = 'paused' THEN 'dry_run' ELSE status END
                WHERE import_run_uuid = ?
                """,
                (import_run_uuid,),
            )
        return update_progress(
            self.connection,
            import_run_uuid,
            paused=0,
            cancelled=0,
            throttled=0,
            throttle_reason=None,
            phase="resumed",
        )

    def cancel(self, import_run_uuid: str) -> dict[str, Any]:
        with self.connection:
            self.connection.execute(
                "UPDATE import_runs SET status = 'cancelled' WHERE import_run_uuid = ?",
                (import_run_uuid,),
            )
        return update_progress(
            self.connection,
            import_run_uuid,
            cancelled=1,
            phase="cancelled",
        )

    def throttle_state(self) -> dict[str, Any]:
        metrics = get_write_actor(self.settings.db_path).diagnostics()
        state = should_throttle_import(
            int(metrics["queue_depth"]),
            self.settings.import_max_write_queue_depth,
        )
        return {
            "throttled": state.throttled,
            "throttle_reason": state.reason,
            "write_queue_depth": metrics["queue_depth"],
        }

    def should_pause_for_backpressure(self, import_run_uuid: str) -> bool:
        state = self.throttle_state()
        if not state["throttled"]:
            return False
        self.pause(import_run_uuid, str(state["throttle_reason"]))
        return True

    def is_paused_or_cancelled(self, import_run_uuid: str) -> bool:
        progress = get_progress(self.connection, import_run_uuid)
        return bool(progress["paused"] or progress["cancelled"])
