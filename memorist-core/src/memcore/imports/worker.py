from __future__ import annotations

import logging
import os
import socket
import threading
import uuid
from dataclasses import dataclass
from typing import Any

from memcore.config import Settings
from memcore.imports.processing import ImportMessageProcessor
from memcore.imports.runtime import import_connection

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class ImportReconstructionWorkerConfig:
    enabled: bool
    concurrency: int
    poll_seconds: float
    lease_seconds: int
    heartbeat_seconds: int
    max_attempts: int
    retry_base_seconds: int
    retry_max_seconds: int

    @classmethod
    def from_settings(cls, settings: Settings) -> ImportReconstructionWorkerConfig:
        return cls(
            enabled=settings.import_reconstruction_worker_enabled,
            concurrency=settings.import_reconstruction_concurrency,
            poll_seconds=float(settings.import_reconstruction_poll_seconds),
            lease_seconds=settings.import_reconstruction_lease_seconds,
            heartbeat_seconds=settings.import_reconstruction_heartbeat_seconds,
            max_attempts=settings.import_reconstruction_max_attempts,
            retry_base_seconds=settings.import_reconstruction_retry_base_seconds,
            retry_max_seconds=settings.import_reconstruction_retry_max_seconds,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "deployment_model": "fastapi_lifespan_background_service",
            "concurrency": self.concurrency,
            "poll_seconds": self.poll_seconds,
            "lease_seconds": self.lease_seconds,
            "heartbeat_seconds": self.heartbeat_seconds,
            "max_attempts": self.max_attempts,
            "retry_base_seconds": self.retry_base_seconds,
            "retry_max_seconds": self.retry_max_seconds,
        }


class ImportReconstructionWorkerService:
    """Durable automatic worker for full import reconstruction.

    The service intentionally claims exactly one message per worker loop and runs
    provider inference outside the claim transaction. FastAPI lifespan owns this
    service in this track; deployments that run multiple web worker processes
    must disable the embedded worker and run a single dedicated process instead.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.config = ImportReconstructionWorkerConfig.from_settings(settings)
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    def start(self) -> None:
        if not self.config.enabled:
            LOGGER.info("import reconstruction worker disabled")
            return
        if os.getenv("RUN_MAIN") == "true" or os.getenv("UVICORN_RELOAD") == "true":
            LOGGER.warning("import reconstruction worker not started in reload child")
            return
        if self._threads:
            return
        with import_connection(self.settings) as connection:
            recovered = ImportMessageProcessor(connection, self.settings).recover_expired_leases()
            LOGGER.info("recovered %s expired import reconstruction leases", recovered)
        for index in range(self.config.concurrency):
            thread = threading.Thread(
                target=self._run_loop,
                args=(index,),
                name=f"memorist-import-reconstruction-{index}",
                daemon=True,
            )
            thread.start()
            self._threads.append(thread)
        LOGGER.info("started %s import reconstruction worker threads", len(self._threads))

    def stop(self, timeout: float = 10.0) -> None:
        self._stop.set()
        for thread in self._threads:
            thread.join(timeout=timeout)
        self._threads.clear()

    def _run_loop(self, index: int) -> None:
        worker_id = _worker_id(index)
        while not self._stop.is_set():
            try:
                did_work = self.process_one_claimed_item(worker_id=worker_id)
            except Exception:
                LOGGER.exception("import reconstruction worker iteration failed")
                did_work = False
            if not did_work:
                self._stop.wait(self.config.poll_seconds)

    def process_once(self, worker_id: str | None = None) -> bool:
        """Compatibility wrapper for the authoritative one-item execution primitive."""
        return self.process_one_claimed_item(worker_id=worker_id or _worker_id(0))

    def process_one_claimed_item(
        self,
        *,
        worker_id: str,
        import_run_uuid: str | None = None,
        heartbeat: bool = True,
    ) -> bool:
        """Claim and safely execute one reconstruction item.

        Background workers and the operational ``/process`` route both use this
        primitive, so claiming, pause/cancel checks, heartbeats, lease-owner CAS,
        and terminal finalization cannot drift between orchestration paths.
        """
        owner = worker_id
        with import_connection(self.settings) as connection:
            processor = ImportMessageProcessor(connection, self.settings)
            processor.recover_expired_leases()
            claimed = processor.claim_next(
                owner,
                self.config.lease_seconds,
                import_run_uuid=import_run_uuid,
            )
            if claimed is None:
                processor.finalize_ready_runs(import_run_uuid)
                return False
            run = connection.execute(
                "SELECT status FROM import_runs WHERE import_run_uuid = ?",
                (claimed["import_run_uuid"],),
            ).fetchone()
            progress = connection.execute(
                "SELECT paused, cancelled FROM import_progress WHERE import_run_uuid = ?",
                (claimed["import_run_uuid"],),
            ).fetchone()
            if (
                run is None
                or run["status"] != "processing"
                or (progress is not None and (progress["paused"] or progress["cancelled"]))
            ):
                processor.release_claim(
                    claimed["status_uuid"],
                    owner,
                    claimed.get("processing_attempt_uuid"),
                )
                return False
            lease_heartbeat = (
                _Heartbeat(
                    self.settings,
                    claimed["status_uuid"],
                    owner,
                    claimed.get("processing_attempt_uuid"),
                    self.config,
                )
                if heartbeat
                else None
            )
            if lease_heartbeat is not None:
                lease_heartbeat.start()
            try:
                processor._process_one(claimed)
            finally:
                if lease_heartbeat is not None:
                    lease_heartbeat.stop()
                    if lease_heartbeat.lease_lost:
                        LOGGER.warning(
                            "lease lost while processing import status %s by %s",
                            claimed["status_uuid"],
                            owner,
                        )
                processor.finalize_if_terminal(str(claimed["import_run_uuid"]))
            return True

    def process_next_batch(
        self,
        *,
        import_run_uuid: str,
        limit: int,
        worker_id: str | None = None,
    ) -> dict[str, Any]:
        """Run up to ``limit`` items through the one-item execution primitive."""
        request_owner = worker_id or _worker_id("api")
        for _ in range(max(1, limit)):
            if not self.process_one_claimed_item(
                worker_id=request_owner,
                import_run_uuid=import_run_uuid,
            ):
                break
        with import_connection(self.settings) as connection:
            processor = ImportMessageProcessor(connection, self.settings)
            processor.finalize_if_terminal(import_run_uuid)
            return processor.processing_report(import_run_uuid)


class _Heartbeat:
    def __init__(
        self,
        settings: Settings,
        status_uuid: str,
        worker_id: str,
        attempt_uuid: str | None,
        config: ImportReconstructionWorkerConfig,
    ) -> None:
        self.settings = settings
        self.status_uuid = status_uuid
        self.worker_id = worker_id
        self.attempt_uuid = attempt_uuid
        self.config = config
        self._stop = threading.Event()
        self.lease_lost = False
        self._thread = threading.Thread(
            target=self._run, name="import-reconstruction-heartbeat", daemon=True
        )

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2.0)

    def _run(self) -> None:
        while not self._stop.wait(self.config.heartbeat_seconds):
            with import_connection(self.settings) as connection:
                renewed = ImportMessageProcessor(connection, self.settings).renew_lease(
                    self.status_uuid,
                    self.worker_id,
                    self.attempt_uuid,
                    self.config.lease_seconds,
                )
            if not renewed:
                self.lease_lost = True
                self._stop.set()


def _worker_id(index: int | str) -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{index}:{uuid.uuid4()}"
