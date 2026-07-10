from __future__ import annotations

import logging
import threading
from contextlib import suppress
from typing import Any

from memcore.config import Settings
from memcore.imports.service import ImportService
from memcore.model_control.security import sanitize_error_message
from memcore.storage.migrations import apply_migrations
from memcore.storage.sqlite import connect

logger = logging.getLogger(__name__)


class ImportReconstructionOrchestrator:
    """Drains durable import reconstruction work outside the SQLite write actor."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    def start(self) -> None:
        if not self.settings.import_reconstruction_worker_enabled:
            return
        if self.settings.runtime_profile == "full":
            logger.warning(
                "Full Mode import reconstruction worker requires PostgreSQL import "
                "repositories before it can start"
            )
            return
        for index in range(self.settings.import_reconstruction_concurrency):
            thread = threading.Thread(
                target=self._run_loop,
                name=f"import-reconstruction-{index + 1}",
                daemon=True,
            )
            thread.start()
            self._threads.append(thread)

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        for thread in self._threads:
            thread.join(timeout=timeout)

    def run_once(self) -> dict[str, Any]:
        connection = connect(self.settings.db_path)
        try:
            apply_migrations(connection)
            run_ids = [
                str(row["import_run_uuid"])
                for row in connection.execute(
                    """
                    SELECT import_run_uuid
                    FROM import_runs
                    WHERE status = 'processing'
                    ORDER BY created_at, import_run_uuid
                    """
                ).fetchall()
            ]
            processed = 0
            for run_id in run_ids:
                report = ImportService(
                    connection, self.settings.object_store_path
                ).process_next_batch(
                    run_id,
                    self.settings.import_reconstruction_worker_batch_size,
                )
                processed += int(report.get("processing_jobs_succeeded", 0))
                if self._stop.is_set():
                    break
            return {"runs_seen": len(run_ids), "processed_succeeded_total": processed}
        finally:
            connection.close()

    def _run_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.run_once()
            except Exception as error:
                logger.warning(
                    "import reconstruction worker failed: %s",
                    sanitize_error_message(f"{type(error).__name__}: {error}"),
                )
            with suppress(Exception):
                self._stop.wait(self.settings.import_reconstruction_poll_seconds)
