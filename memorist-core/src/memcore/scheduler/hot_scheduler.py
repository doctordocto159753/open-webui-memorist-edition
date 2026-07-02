from __future__ import annotations

import heapq
import threading
from dataclasses import dataclass, field
from time import monotonic
from typing import Any

from memcore.config import Settings
from memcore.scheduler.backpressure import BackpressureState
from memcore.scheduler.lanes import HOT_LANES, LANE_PRIORITIES, LOW_PRIORITY_LANES, SchedulerLane
from memcore.scheduler.metrics import empty_lane_metrics


@dataclass(frozen=True)
class HotSchedulerJob:
    durable_job_uuid: str
    lane: SchedulerLane
    payload_ref: dict[str, Any] = field(default_factory=dict)
    priority: int | None = None
    submitted_at: float = field(default_factory=monotonic)


@dataclass(order=True)
class _QueuedJob:
    sort_priority: int
    sequence: int
    job: HotSchedulerJob = field(compare=False)


class HotScheduler:
    def __init__(
        self,
        *,
        max_consecutive_low_priority: int = 1,
        hot_lane_target_wait_ms: int = 100,
    ) -> None:
        self.max_consecutive_low_priority = max_consecutive_low_priority
        self.hot_lane_target_wait_ms = hot_lane_target_wait_ms
        self._lock = threading.Lock()
        self._queue: list[_QueuedJob] = []
        self._sequence = 0
        self._active_workers: dict[str, str] = {}
        self._paused_lanes: set[SchedulerLane] = set()
        self._metrics = empty_lane_metrics()
        self._backpressure = BackpressureState()
        self._last_scheduler_error: str | None = None
        self._consecutive_low_priority = 0

    def submit(self, job: HotSchedulerJob) -> None:
        with self._lock:
            self._sequence += 1
            priority = job.priority if job.priority is not None else LANE_PRIORITIES[job.lane]
            heapq.heappush(self._queue, _QueuedJob(-priority, self._sequence, job))
            self._refresh_backpressure_locked()

    def claim_next(self, worker_id: str) -> HotSchedulerJob | None:
        with self._lock:
            index = self._select_next_index_locked()
            if index is None:
                return None
            queued = self._queue.pop(index)
            heapq.heapify(self._queue)
            wait_ms = int((monotonic() - queued.job.submitted_at) * 1000)
            self._metrics[queued.job.lane].record_wait(wait_ms)
            self._active_workers[worker_id] = queued.job.durable_job_uuid
            if queued.job.lane in LOW_PRIORITY_LANES:
                self._consecutive_low_priority += 1
            else:
                self._consecutive_low_priority = 0
            self._refresh_backpressure_locked()
            return queued.job

    def complete(self, worker_id: str) -> None:
        with self._lock:
            self._active_workers.pop(worker_id, None)

    def pause_lane(self, lane: SchedulerLane) -> None:
        with self._lock:
            self._paused_lanes.add(lane)

    def resume_lane(self, lane: SchedulerLane) -> None:
        with self._lock:
            self._paused_lanes.discard(lane)

    def status(self) -> dict[str, Any]:
        with self._lock:
            now = monotonic()
            lane_depths = {lane.value: 0 for lane in SchedulerLane}
            oldest_by_lane = {lane.value: 0 for lane in SchedulerLane}
            for queued in self._queue:
                lane = queued.job.lane
                lane_depths[lane.value] += 1
                age_ms = int((now - queued.job.submitted_at) * 1000)
                oldest_by_lane[lane.value] = max(oldest_by_lane[lane.value], age_ms)
            return {
                "enabled": True,
                "lane_depths": lane_depths,
                "oldest_job_age_ms_by_lane": oldest_by_lane,
                "p50_wait_ms_by_lane": {
                    lane.value: metrics.p50() for lane, metrics in self._metrics.items()
                },
                "p95_wait_ms_by_lane": {
                    lane.value: metrics.p95() for lane, metrics in self._metrics.items()
                },
                "backpressure": self._backpressure.as_dict(),
                "paused_lanes": sorted(lane.value for lane in self._paused_lanes),
                "active_workers": dict(self._active_workers),
                "last_scheduler_error": self._last_scheduler_error,
            }

    def _select_next_index_locked(self) -> int | None:
        candidates = [
            (index, queued)
            for index, queued in enumerate(self._queue)
            if queued.job.lane not in self._paused_lanes
        ]
        if not candidates:
            return None

        hot_candidates = [
            (index, queued) for index, queued in candidates if queued.job.lane in HOT_LANES
        ]
        if (
            hot_candidates
            and self._consecutive_low_priority >= self.max_consecutive_low_priority
        ):
            return min(
                hot_candidates,
                key=lambda item: (item[1].sort_priority, item[1].sequence),
            )[0]

        return min(candidates, key=lambda item: (item[1].sort_priority, item[1].sequence))[0]

    def _refresh_backpressure_locked(self) -> None:
        hot_depth = sum(1 for queued in self._queue if queued.job.lane in HOT_LANES)
        low_depth = sum(1 for queued in self._queue if queued.job.lane in LOW_PRIORITY_LANES)
        if hot_depth:
            self._backpressure = BackpressureState(
                "hot_lane_pending",
                "hot lanes have runnable work",
            )
        elif low_depth > 100:
            self._backpressure = BackpressureState(
                "low_priority_backlog",
                "background backlog high",
            )
        else:
            self._backpressure = BackpressureState()


_SCHEDULERS: dict[str, HotScheduler] = {}
_SCHEDULERS_LOCK = threading.Lock()


def get_hot_scheduler(settings: Settings) -> HotScheduler | None:
    if settings.hot_scheduler == "disabled":
        return None
    key = f"{settings.runtime_profile}:{settings.canonical_store}"
    with _SCHEDULERS_LOCK:
        scheduler = _SCHEDULERS.get(key)
        if scheduler is None:
            scheduler = HotScheduler(
                max_consecutive_low_priority=settings.scheduler_max_consecutive_low_priority,
                hot_lane_target_wait_ms=settings.hot_lane_target_wait_ms,
            )
            _SCHEDULERS[key] = scheduler
        return scheduler
