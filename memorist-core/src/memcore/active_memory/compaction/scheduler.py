import sqlite3

from memcore.active_memory.materializer import ActiveMemoryMaterializer, source_snapshot_hash
from memcore.active_memory.repositories import ActiveMemoryRepository
from memcore.active_memory.selectors import select_canonical_sources, session_hot_cache_items
from memcore.repositories import JobRepository


def register_block_jobs(connection: sqlite3.Connection) -> None:
    jobs = JobRepository(connection)
    for job_type in ("block_rebuild", "block_compaction", "block_source_invalidation"):
        jobs.enqueue_job_once(job_type, {"registered": True}, priority=40)


def rebuild_stale_blocks(connection: sqlite3.Connection) -> list[dict[str, object]]:
    repository = ActiveMemoryRepository(connection)
    rows = connection.execute(
        "SELECT block_uuid FROM memory_blocks WHERE status = 'active'"
    ).fetchall()
    rebuilt: list[dict[str, object]] = []
    for row in rows:
        block = repository.get_block(row["block_uuid"])
        sources = select_canonical_sources(connection, block)
        hot_cache_items = session_hot_cache_items(connection, block)
        if block.source_snapshot_hash != source_snapshot_hash(sources, hot_cache_items):
            result = ActiveMemoryMaterializer(connection).build(
                block.block_uuid,
                trigger_type="stale_rebuild",
                actor_type="system",
            )
            rebuilt.append(result.model_dump(mode="json"))
    return rebuilt
