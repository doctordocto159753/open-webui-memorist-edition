import sqlite3
from collections.abc import Callable
from typing import Any, Protocol

from memcore.config import Settings
from memcore.memory_worker.fencing import (
    LeaseFenceRejected,
    close_transaction_before_provider,
    fenced_write,
)
from memcore.repositories.memory_worker import MemoryStoreRepository


class GraphProjector(Protocol):
    def project(self, payload: dict[str, Any]) -> None: ...


class DisabledGraphProjector:
    def project(self, payload: dict[str, Any]) -> None:
        return None


class FalkorDBGraphProjector:
    def __init__(self, url: str) -> None:
        self.url = url

    def project(self, payload: dict[str, Any]) -> None:
        raise RuntimeError("FalkorDB projection is not implemented in Phase 2 local lite mode")


class GraphProjectionRunner:
    def __init__(self, connection: sqlite3.Connection, settings: Settings) -> None:
        self.connection = connection
        self.settings = settings
        self.store = MemoryStoreRepository(connection)
        self.projector: GraphProjector = (
            DisabledGraphProjector()
            if settings.graph_backend == "disabled"
            else FalkorDBGraphProjector(settings.falkordb_url or "")
        )

    def run_once(
        self,
        limit: int = 100,
        *,
        lease_fence: Callable[..., None] | None = None,
    ) -> dict[str, int]:
        processed = 0
        retry = 0
        for item in self.store.list_pending_outbox(limit):
            try:
                close_transaction_before_provider(self.connection, lease_fence)
                self.projector.project(
                    {"outbox_uuid": item.outbox_uuid, "payload": item.payload_ijson}
                )
            except LeaseFenceRejected:
                raise
            except Exception as error:
                with fenced_write(self.connection, lease_fence):
                    self.store.mark_outbox_retry(item.outbox_uuid, str(error))
                retry += 1
                continue
            with fenced_write(self.connection, lease_fence):
                self.store.mark_outbox_succeeded(item.outbox_uuid)
            processed += 1
        return {"processed": processed, "retry": retry}
