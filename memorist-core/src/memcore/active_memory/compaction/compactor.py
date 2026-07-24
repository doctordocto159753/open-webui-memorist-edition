import sqlite3

from memcore.active_memory.compaction.coverage import validate_coverage
from memcore.active_memory.compaction.deterministic import deterministic_compact
from memcore.active_memory.materializer import ActiveMemoryMaterializer
from memcore.active_memory.repositories import ActiveMemoryRepository
from memcore.active_memory.selectors import select_canonical_sources
from memcore.model_control.repository import ModelControlRepository
from memcore.model_control.stage_contracts import (
    deterministic_compaction,
    validate_compaction_result,
)
from memcore.model_control.stage_invocation import StageInvocationRequest, StageInvoker
from memcore.models import ModelRole
from memcore.repositories.domain import RepositoryError


class BlockCompactor:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self.repository = ActiveMemoryRepository(connection)

    def compact(self, block_uuid: str, actor_type: str = "user") -> dict[str, object]:
        block = self.repository.get_block(block_uuid)
        sources = select_canonical_sources(self.connection, block)
        selected, deterministic_omissions = deterministic_compact(block, sources)
        stage = StageInvoker(
            self.connection,
            ModelControlRepository(self.connection),
        ).invoke_structured(
            StageInvocationRequest(
                role=ModelRole.BLOCK_COMPACTION,
                stage="block_compaction",
                source_type="memory_block",
                source_uuid=block_uuid,
                project_uuid=block.scope_uuid if block.scope_type == "project" else None,
                workspace_uuid=block.scope_uuid if block.scope_type == "workspace" else None,
                prompt_id="memorist.block_compaction",
                prompt_version="2.0",
                input_payload={
                    "block_type": block.block_type.value,
                    "char_limit": block.char_limit,
                    "sources": [source.model_dump(mode="json") for source in selected],
                },
            ),
            validator=validate_compaction_result,
            deterministic_output=deterministic_compaction,
        )
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
            "processing_node": stage.model_dump(mode="json", exclude={"output"}),
        }
