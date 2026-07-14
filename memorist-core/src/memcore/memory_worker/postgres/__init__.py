from memcore.memory_worker.postgres.deterministic_fallback import (
    deterministic_jakobson_output,
)
from memcore.memory_worker.postgres.gated_candidate_adapter import (
    record_candidates,
)
from memcore.memory_worker.postgres.pipeline import PostgresMemoryWorkerPipeline
from memcore.memory_worker.postgres.routing_policy_adapter import record_routes

setattr(
    PostgresMemoryWorkerPipeline,
    "_deterministic_jakobson_output",
    deterministic_jakobson_output,
)
setattr(PostgresMemoryWorkerPipeline, "_record_routes", record_routes)
setattr(PostgresMemoryWorkerPipeline, "_record_candidates", record_candidates)

__all__ = ["PostgresMemoryWorkerPipeline"]
