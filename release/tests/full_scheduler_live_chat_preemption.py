from __future__ import annotations

import time
from typing import Any

from release.tests.full_mode_common import (
    SkipSmoke,
    apply_postgres_schema,
    connect_postgres,
    failed_result,
    passed_result,
    postgres_service,
    print_result,
    sanitize,
    test_namespace,
)

NAME = "full_scheduler_live_chat_preemption"


def run() -> dict[str, Any]:
    try:
        with postgres_service(NAME) as postgres:
            from memcore.scheduler.hot_scheduler import HotScheduler, HotSchedulerJob
            from memcore.scheduler.lanes import SchedulerLane
            from memcore.storage.postgres.jobs import PostgresJobRepository

            connection = connect_postgres(postgres.url)
            namespace = test_namespace("scheduler")
            try:
                apply_postgres_schema(connection)
                durable = PostgresJobRepository(connection)
                import_job = durable.enqueue_job(
                    "import_commit",
                    {"namespace": namespace},
                    priority=40,
                    idempotency_key=f"{namespace}-import-1",
                )
                graph_job = durable.enqueue_job(
                    "graph_projection",
                    {"namespace": namespace},
                    priority=25,
                    idempotency_key=f"{namespace}-graph-1",
                )
                embedding_job = durable.enqueue_job(
                    "embedding_index",
                    {"namespace": namespace},
                    priority=25,
                    idempotency_key=f"{namespace}-embedding-1",
                )
                live_job = durable.enqueue_job(
                    "live_chat_capture",
                    {"namespace": namespace},
                    priority=100,
                    idempotency_key=f"{namespace}-live-1",
                )
                privacy_job = durable.enqueue_job(
                    "critical_privacy",
                    {"namespace": namespace},
                    priority=110,
                    idempotency_key=f"{namespace}-privacy-1",
                )
            finally:
                connection.close()

            scheduler = HotScheduler(max_consecutive_low_priority=1)
            scheduler.submit(
                HotSchedulerJob(str(import_job["job_uuid"]), SchedulerLane.IMPORT_COMMIT)
            )
            first = scheduler.claim_next("worker")
            if first is None or first.lane is not SchedulerLane.IMPORT_COMMIT:
                return failed_result(
                    NAME,
                    "initial_import_claim",
                    "Expected import batch to run when no hot lane exists.",
                )
            scheduler.complete("worker")
            scheduler.submit(HotSchedulerJob(str(graph_job["job_uuid"]), SchedulerLane.GRAPH_PROJECTION))
            scheduler.submit(HotSchedulerJob(str(embedding_job["job_uuid"]), SchedulerLane.EMBEDDING_INDEX))
            live_submitted = time.perf_counter()
            scheduler.submit(
                HotSchedulerJob(str(live_job["job_uuid"]), SchedulerLane.LIVE_CHAT_CAPTURE)
            )
            scheduler.submit(
                HotSchedulerJob(str(privacy_job["job_uuid"]), SchedulerLane.CRITICAL_PRIVACY)
            )
            second = scheduler.claim_next("worker")
            scheduler.complete("worker")
            third = scheduler.claim_next("worker")
            live_wait_ms = int((time.perf_counter() - live_submitted) * 1000)
            status = scheduler.status()
            if (
                second is None
                or third is None
                or second.lane is not SchedulerLane.CRITICAL_PRIVACY
                or third.lane is not SchedulerLane.LIVE_CHAT_CAPTURE
            ):
                return failed_result(
                    NAME,
                    "preemption_order",
                    "Critical privacy must preempt import; live chat must run before more low-priority work.",
                    second_lane=None if second is None else second.lane.value,
                    third_lane=None if third is None else third.lane.value,
                    scheduler=status,
                )
            return passed_result(
                NAME,
                postgres_provisioned_by=postgres.provisioned_by,
                lane_depth=status["lane_depths"],
                oldest_job_age=status["oldest_job_age_ms_by_lane"],
                p95_wait_ms=status["p95_wait_ms_by_lane"],
                live_chat_wait_ms=live_wait_ms,
                low_priority_yield_count=1,
                hot_lane_preemptions=2,
                backpressure_state=status["backpressure"],
            )
    except SkipSmoke as skip:
        return skip.result
    except Exception as error:
        return failed_result(
            NAME,
            "exception",
            "Run with PostgreSQL DSN and inspect scheduler status output.",
            error_sanitized=sanitize(str(error)),
        )


def main() -> int:
    return print_result(run())


if __name__ == "__main__":
    raise SystemExit(main())
