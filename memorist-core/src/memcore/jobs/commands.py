from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from memcore.repositories import JobRepository
from memcore.storage.commands import WriteResult


@dataclass(frozen=True)
class ClaimJobCommand:
    command_type: str = "claim_job"
    idempotency_key: str | None = None

    def execute(self, connection: sqlite3.Connection) -> WriteResult:
        job = JobRepository(connection).claim_next_job()
        return WriteResult(
            command_type=self.command_type,
            target_type="job" if job else None,
            target_uuid=job.job_uuid if job else None,
            result={"job": job.model_dump(mode="json") if job else None},
        )
