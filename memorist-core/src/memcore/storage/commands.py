from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class WriteResult:
    command_type: str
    target_type: str | None
    target_uuid: str | None
    result: dict[str, Any]
    idempotent_replay: bool = False
    queue_wait_ms: int = 0
    transaction_ms: int = 0


class WriteCommand(Protocol):
    @property
    def command_type(self) -> str:
        ...

    @property
    def idempotency_key(self) -> str | None:
        ...

    def execute(self, connection: sqlite3.Connection) -> WriteResult:
        ...


DEFAULT_COMMAND_PRIORITY = 50
DEFAULT_MAX_TRANSACTION_MS = 30_000
DEFAULT_MAX_WRITE_WEIGHT = 20_000


def command_priority(command: WriteCommand) -> int:
    """Return command priority; higher values are executed first."""

    return int(getattr(command, "priority", DEFAULT_COMMAND_PRIORITY))


def command_estimated_write_weight(command: WriteCommand) -> int:
    """Return an approximate write cost used to reject unsafe giant commands."""

    return int(getattr(command, "estimated_write_weight", 1))


def command_max_transaction_ms(command: WriteCommand) -> int:
    """Return the command's bounded transaction budget in milliseconds."""

    return int(getattr(command, "max_transaction_ms", DEFAULT_MAX_TRANSACTION_MS))


def command_can_batch(command: WriteCommand) -> bool:
    """Return whether the command can be grouped by higher-level schedulers."""

    return bool(getattr(command, "can_batch", True))


@dataclass(frozen=True)
class BatchPolicy:
    batch_size: int = 100
    continue_on_error: bool = True
    max_queue_depth: int = 500


@dataclass(frozen=True)
class FunctionWriteCommand:
    command_type: str
    handler: Any
    idempotency_key: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    priority: int = DEFAULT_COMMAND_PRIORITY
    estimated_write_weight: int = 1
    max_transaction_ms: int = DEFAULT_MAX_TRANSACTION_MS
    can_batch: bool = True

    def execute(self, connection: sqlite3.Connection) -> WriteResult:
        result = self.handler(connection)
        if not isinstance(result, dict):
            result = {"value": result}
        return WriteResult(
            command_type=self.command_type,
            target_type=self.metadata.get("target_type"),
            target_uuid=self.metadata.get("target_uuid"),
            result=result,
        )
