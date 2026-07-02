from __future__ import annotations

from typing import Any

from memcore.models import Job, Message
from memcore.storage.commands import WriteCommand
from memcore.storage.write_actor import get_write_actor


class WriteGateway:
    def __init__(self, db_path: str) -> None:
        self.actor = get_write_actor(db_path)

    def submit(self, command: WriteCommand, timeout: float | None = None) -> dict[str, Any]:
        return self.actor.submit_sync(command, timeout).result

    def create_message(self, command: WriteCommand, timeout: float | None = None) -> Message:
        result = self.actor.submit_sync(command, timeout).result
        return Message.model_validate(result["message"])

    def claim_job(self, command: WriteCommand, timeout: float | None = None) -> Job | None:
        result = self.actor.submit_sync(command, timeout).result
        job = result.get("job")
        return Job.model_validate(job) if isinstance(job, dict) else None
