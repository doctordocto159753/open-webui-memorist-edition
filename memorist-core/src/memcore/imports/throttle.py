from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ImportThrottleState:
    throttled: bool
    reason: str | None = None


def should_throttle_import(
    write_queue_depth: int,
    max_write_queue_depth: int,
) -> ImportThrottleState:
    if write_queue_depth >= max_write_queue_depth:
        return ImportThrottleState(True, "write_queue_depth")
    return ImportThrottleState(False)
