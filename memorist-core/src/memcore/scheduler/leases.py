from __future__ import annotations

from dataclasses import dataclass
from time import monotonic


@dataclass(frozen=True)
class SchedulerLease:
    job_uuid: str
    worker_id: str
    acquired_at: float
    lease_seconds: int

    def expired(self) -> bool:
        return monotonic() - self.acquired_at > self.lease_seconds
