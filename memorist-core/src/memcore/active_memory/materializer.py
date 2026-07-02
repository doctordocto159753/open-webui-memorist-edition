import sqlite3
from collections.abc import Sequence

from memcore.active_memory.policies import actor_can_build_block, source_role_for
from memcore.active_memory.renderer import render_block, structured_block, token_count
from memcore.active_memory.repositories import ActiveMemoryRepository
from memcore.active_memory.schemas import BlockBuildRun, BlockVersion, BuildResult, CoverageReport
from memcore.active_memory.selectors import select_canonical_sources, session_hot_cache_items
from memcore.models import utc_now
from memcore.repositories.domain import RepositoryError
from memcore.validators.ijson import canonical_hash_ijson, dump_ijson

BUILDER_VERSION = "deterministic-active-memory-v1"


class ActiveMemoryMaterializer:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self.repository = ActiveMemoryRepository(connection)

    def build(
        self,
        block_uuid: str,
        trigger_type: str = "manual",
        actor_type: str = "user",
        expected_optimistic_lock_version: int | None = None,
    ) -> BuildResult:
        block = self.repository.get_block(block_uuid)
        if not actor_can_build_block(block, actor_type):
            raise RepositoryError("actor is not authorized to build this memory block")
        if (
            expected_optimistic_lock_version is not None
            and block.optimistic_lock_version != expected_optimistic_lock_version
        ):
            raise RepositoryError("memory block optimistic lock changed")

        sources = select_canonical_sources(self.connection, block)
        hot_cache_items = session_hot_cache_items(self.connection, block)
        snapshot_hash = source_snapshot_hash(sources, hot_cache_items)
        run = self.repository.create_build_run(
            BlockBuildRun(
                block_uuid=block_uuid,
                builder_version=BUILDER_VERSION,
                source_snapshot_hash=snapshot_hash,
                trigger_type=trigger_type,
                started_at=utc_now(),
            )
        )

        current_block = self.repository.get_block(block_uuid)
        if current_block.optimistic_lock_version != block.optimistic_lock_version:
            self.repository.update_build_run(
                run.block_build_run_uuid,
                {
                    "status": "failed",
                    "completed_at": utc_now(),
                    "error_text": "optimistic lock changed",
                },
            )
            raise RepositoryError("memory block optimistic lock changed")
        if (
            source_snapshot_hash(select_canonical_sources(self.connection, block), hot_cache_items)
            != snapshot_hash
        ):
            self.repository.update_build_run(
                run.block_build_run_uuid,
                {
                    "status": "failed",
                    "completed_at": utc_now(),
                    "error_text": "source snapshot changed",
                },
            )
            raise RepositoryError("source snapshot changed during build")

        value, omitted_sources = render_block(block, sources, hot_cache_items)
        version = self.repository.create_version(
            BlockVersion(
                block_uuid=block.block_uuid,
                version_number=self.repository.next_version_number(block.block_uuid),
                value=value,
                structured_value_ijson=dump_ijson(
                    structured_block(sources, hot_cache_items, omitted_sources)
                ),
                char_count=len(value),
                token_count=token_count(value),
                source_snapshot_hash=snapshot_hash,
                block_build_run_uuid=run.block_build_run_uuid,
                change_reason=trigger_type,
                created_by=actor_type,
            ),
            sources,
            {source.memory_version_uuid: source_role_for(block, source) for source in sources},
        )
        self.repository.update_current_version(block, version)
        self.repository.update_build_run(
            run.block_build_run_uuid,
            {
                "status": "succeeded",
                "completed_at": utc_now(),
                "raw_output_ijson": dump_ijson(
                    {"value": value, "omitted_sources": omitted_sources}
                ),
            },
        )
        return BuildResult(
            block_uuid=block.block_uuid,
            block_version_uuid=version.block_version_uuid,
            version_number=version.version_number,
            source_snapshot_hash=snapshot_hash,
            value=value,
            omitted_sources=omitted_sources,
        )

    def coverage(self, block_uuid: str) -> CoverageReport:
        block = self.repository.get_block(block_uuid)
        sources = select_canonical_sources(self.connection, block)
        hot_cache_items = session_hot_cache_items(self.connection, block)
        current_hash = source_snapshot_hash(sources, hot_cache_items)
        current_sources = self.repository.list_sources(block_uuid)
        covered = {row["memory_version_uuid"] for row in current_sources}
        required = {source.memory_version_uuid for source in sources}
        source_coverage = 1.0 if not required else len(covered & required) / len(required)
        constraint_sources = {
            source.memory_version_uuid for source in sources if source.memory_type == "constraint"
        }
        constraint_coverage = (
            1.0
            if not constraint_sources
            else len(covered & constraint_sources) / len(constraint_sources)
        )
        return CoverageReport(
            block_uuid=block_uuid,
            source_coverage=source_coverage,
            required_item_coverage=source_coverage,
            constraint_coverage=constraint_coverage,
            unsupported_assertion_count=0,
            conflict_preservation=True,
            char_count=len(block.value),
            token_count=token_count(block.value),
            omitted_sources=[],
            stale=block.source_snapshot_hash != current_hash,
        )


def source_snapshot_hash(
    sources: Sequence[object],
    hot_cache_items: list[dict[str, object]],
) -> str:
    source_values = [
        source.model_dump(mode="json", exclude_none=True)
        if hasattr(source, "model_dump")
        else source
        for source in sources
    ]
    return canonical_hash_ijson({"sources": source_values, "hot_cache": hot_cache_items})
