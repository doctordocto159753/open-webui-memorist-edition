import sqlite3

from memcore.active_memory.compaction.coverage import validate_coverage
from memcore.active_memory.compaction.deterministic import deterministic_compact
from memcore.active_memory.materializer import ActiveMemoryMaterializer
from memcore.active_memory.repositories import ActiveMemoryRepository
from memcore.active_memory.selectors import select_canonical_sources
from memcore.repositories.domain import RepositoryError


class BlockCompactor:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self.repository = ActiveMemoryRepository(connection)

    def compact(self, block_uuid: str, actor_type: str = "user") -> dict[str, object]:
        block = self.repository.get_block(block_uuid)
        sources = select_canonical_sources(self.connection, block)
        selected, deterministic_omissions = deterministic_compact(block, sources)
        result = ActiveMemoryMaterializer(self.connection).build(
            block_uuid,
            trigger_type="compaction",
            actor_type=actor_type,
            expected_optimistic_lock_version=block.optimistic_lock_version,
        )
        errors = validate_coverage(selected, result.value)
        if errors:
            raise RepositoryError("; ".join(errors))
        return {
            **result.model_dump(mode="json"),
            "deterministic_omissions": deterministic_omissions,
        }
