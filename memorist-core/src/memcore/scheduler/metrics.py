from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from memcore.scheduler.lanes import SchedulerLane


@dataclass
class LaneMetrics:
    waits_ms: deque[int] = field(default_factory=lambda: deque(maxlen=512))

    def record_wait(self, wait_ms: int) -> None:
        self.waits_ms.append(wait_ms)

    def p50(self) -> int:
        return _percentile(list(self.waits_ms), 50)

    def p95(self) -> int:
        return _percentile(list(self.waits_ms), 95)


def empty_lane_metrics() -> dict[SchedulerLane, LaneMetrics]:
    return {lane: LaneMetrics() for lane in SchedulerLane}


def _percentile(values: list[int], percentile: int) -> int:
    if not values:
        return 0
    sorted_values = sorted(values)
    index = int(round((percentile / 100) * (len(sorted_values) - 1)))
    return sorted_values[index]
