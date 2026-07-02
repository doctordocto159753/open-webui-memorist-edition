from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from memcore.governance.privacy import PrivacyService
from memcore.storage.commands import WriteResult
from memcore.storage.write_actor import get_write_actor

PrivacyAction = Literal["confirm", "execute", "retry"]


@dataclass(frozen=True)
class PrivacyRequestCommand:
    request_uuid: str
    action: PrivacyAction
    confirmation_value: str | None = None
    command_type: str = "privacy.request"
    priority: int = 70
    estimated_write_weight: int = 250
    max_transaction_ms: int = 30_000
    can_batch: bool = False

    @property
    def idempotency_key(self) -> str | None:
        return None

    def execute(self, connection: sqlite3.Connection) -> WriteResult:
        service = PrivacyService(connection)
        if self.action == "confirm":
            if not self.confirmation_value:
                raise ValueError("confirmation_token is required")
            result = service.confirm_request(self.request_uuid, self.confirmation_value)
        elif self.action == "execute":
            result = service.execute_request(self.request_uuid)
        else:
            result = service.retry_request(self.request_uuid)
        return WriteResult(
            command_type=f"{self.command_type}.{self.action}",
            target_type="privacy_request",
            target_uuid=self.request_uuid,
            result=result,
        )


def run_privacy_request_command(
    db_path: str | Path,
    request_uuid: str,
    action: PrivacyAction,
    confirmation_value: str | None = None,
    timeout: float | None = 120,
) -> dict[str, Any]:
    result = get_write_actor(db_path).submit_sync(
        PrivacyRequestCommand(
            request_uuid=request_uuid,
            action=action,
            confirmation_value=confirmation_value,
        ),
        timeout=timeout,
    )
    return result.result
