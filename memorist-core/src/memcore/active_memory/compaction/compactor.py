import sqlite3
from collections.abc import Callable

from memcore.active_memory.compaction.coverage import validate_coverage
from memcore.active_memory.compaction.deterministic import deterministic_compact
from memcore.active_memory.compaction.proposal import (
    COMPACTION_POLICY_VERSION,
    validate_and_render_proposal,
)
from memcore.active_memory.materializer import (
    ActiveMemoryMaterializer,
    source_snapshot_hash,
)
from memcore.active_memory.repositories import ActiveMemoryRepository
from memcore.active_memory.selectors import (
    select_canonical_sources,
    session_hot_cache_items,
)
from memcore.memory_worker.fencing import fenced_write
from memcore.model_control.repository import ModelControlRepository
from memcore.model_control.stage_contracts import (
    deterministic_compaction,
    validate_compaction_result,
)
from memcore.model_control.stage_invocation import StageInvocationRequest, StageInvoker
from memcore.models import ModelRole
from memcore.repositories.domain import RepositoryError
from memcore.validators.ijson import dump_ijson


class BlockCompactor:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self.repository = ActiveMemoryRepository(connection)

    def compact(
        self,
        block_uuid: str,
        actor_type: str = "user",
        lease_fence: Callable[..., None] | None = None,
    ) -> dict[str, object]:
        block = self.repository.get_block(block_uuid)
        sources = select_canonical_sources(self.connection, block)
        initial_source_snapshot_hash = source_snapshot_hash(
            sources,
            session_hot_cache_items(self.connection, block),
        )
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
                    "compaction_policy_version": COMPACTION_POLICY_VERSION,
                    "source_snapshot_hash": initial_source_snapshot_hash,
                    "sources": [
                        source.model_dump(mode="json", exclude_none=True)
                        for source in selected
                    ],
                },
            ),
            validator=validate_compaction_result,
            deterministic_output=deterministic_compaction,
            lease_fence=lease_fence,
        )
        fallback_reason: str | None = None
        provider_output_applied = False
        try:
            if stage.status not in {"ok"} or stage.output is None:
                raise ValueError("configured compaction provider did not produce accepted output")
            proposal = validate_and_render_proposal(block, selected, stage.output)
            provider_output_applied = stage.provider_type not in {"deterministic", "disabled"}
            if not provider_output_applied:
                proposal.structured_value["content_source"] = "deterministic_fallback"
                fallback_reason = stage.fallback_reason or "built_in_deterministic_compaction"
        except ValueError as error:
            fallback_reason = str(error)
            if stage.status == "ok":
                with fenced_write(self.connection, lease_fence):
                    self.connection.execute(
                        """
                        UPDATE processing_stage_runs
                        SET status = 'abstained', detail_sanitized = ?,
                            validation_errors_ijson = ?
                        WHERE stage_execution_uuid = ?
                        """,
                        (
                            "context validation rejected compaction proposal",
                            dump_ijson([type(error).__name__]),
                            stage.execution_uuid,
                        ),
                    )
                stage = stage.model_copy(
                    update={
                        "status": "abstained",
                        "detail_sanitized": "context validation rejected compaction proposal",
                        "validation_errors": [type(error).__name__],
                    }
                )
            if block.current_version_uuid is not None:
                return {
                    "block_uuid": block.block_uuid,
                    "block_version_uuid": block.current_version_uuid,
                    "value": block.value,
                    "published": False,
                    "provider_output_applied": False,
                    "fallback": "previous_valid_block_preserved",
                    "fallback_reason": fallback_reason,
                    "deterministic_omissions": deterministic_omissions,
                    "processing_node": stage.model_dump(mode="json", exclude={"output"}),
                }
            deterministic_output = deterministic_compaction(
                {
                    "sources": [
                        source.model_dump(mode="json", exclude_none=True)
                        for source in selected
                    ],
                }
            )
            proposal = validate_and_render_proposal(block, selected, deterministic_output)
            proposal.structured_value["content_source"] = "deterministic_fallback"
            fallback_reason = "provider_invalid_without_previous_block"

        try:
            with fenced_write(self.connection, lease_fence):
                result = ActiveMemoryMaterializer(self.connection).build(
                    block_uuid,
                    trigger_type="compaction",
                    actor_type=actor_type,
                    expected_optimistic_lock_version=block.optimistic_lock_version,
                    compaction_proposal=proposal,
                    prompt_execution_uuid=stage.execution_uuid,
                    builder_version=COMPACTION_POLICY_VERSION,
                    expected_source_snapshot_hash=initial_source_snapshot_hash,
                )
        except RepositoryError as error:
            if block.current_version_uuid is None:
                raise
            return {
                "block_uuid": block.block_uuid,
                "block_version_uuid": block.current_version_uuid,
                "value": block.value,
                "published": False,
                "provider_output_applied": False,
                "fallback": "previous_valid_block_preserved",
                "fallback_reason": str(error),
                "deterministic_omissions": deterministic_omissions,
                "processing_node": stage.model_dump(mode="json", exclude={"output"}),
            }
        errors = validate_coverage(proposal.sources, result.value)
        if errors:
            raise RepositoryError("; ".join(errors))
        return {
            **result.model_dump(mode="json"),
            "published": True,
            "provider_output_applied": provider_output_applied,
            "fallback": (
                "deterministic_compaction" if not provider_output_applied else None
            ),
            "fallback_reason": fallback_reason,
            "deterministic_omissions": deterministic_omissions,
            "processing_node": stage.model_dump(mode="json", exclude={"output"}),
        }
