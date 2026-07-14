from memcore.memory_worker.postgres.deterministic_fallback import (
    deterministic_jakobson_output,
)
from memcore.memory_worker.postgres.pipeline import PostgresMemoryWorkerPipeline

setattr(
    PostgresMemoryWorkerPipeline,
    "_deterministic_jakobson_output",
    deterministic_jakobson_output,
)

__all__ = ["PostgresMemoryWorkerPipeline"]
