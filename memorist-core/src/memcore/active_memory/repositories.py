from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from typing import Any

from memcore.active_memory.schemas import BlockBuildRun, BlockSource, BlockVersion
from memcore.models import MemoryBlock, utc_now
from memcore.repositories.domain import MemoryBlockRepository, RepositoryError
from memcore.repositories.sqlite import SQLiteRepository


class ActiveMemoryRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self.sqlite = SQLiteRepository(connection)
        self.blocks = MemoryBlockRepository(connection)

    def get_block(self, block_uuid: str) -> MemoryBlock:
        block = self.blocks.get_block(block_uuid)
        if block is None:
            raise RepositoryError(f"Memory block not found: {block_uuid}")
        return block

    def create_build_run(self, run: BlockBuildRun) -> BlockBuildRun:
        with self.connection:
            self.sqlite.insert("memory_block_build_runs", run.model_dump(mode="json"))
        return run

    def update_build_run(self, run_uuid: str, values: Mapping[str, Any]) -> None:
        with self.connection:
            self.sqlite.update(
                "memory_block_build_runs",
                values,
                "block_build_run_uuid = ?",
                (run_uuid,),
            )

    def next_version_number(self, block_uuid: str) -> int:
        row = self.connection.execute(
            """
            SELECT COALESCE(MAX(version_number), 0) + 1 AS next
            FROM memory_block_versions
            WHERE block_uuid = ?
            """,
            (block_uuid,),
        ).fetchone()
        return int(row["next"])

    def create_version(
        self,
        version: BlockVersion,
        sources: list[BlockSource],
        source_roles: dict[str, str],
    ) -> BlockVersion:
        with self.connection:
            self.sqlite.insert("memory_block_versions", version.model_dump(mode="json"))
            for source in sources:
                self.sqlite.insert(
                    "memory_block_sources",
                    {
                        "block_version_uuid": version.block_version_uuid,
                        "memory_uuid": source.memory_uuid,
                        "memory_version_uuid": source.memory_version_uuid,
                        "source_role": source_roles[source.memory_version_uuid],
                        "inclusion_reason": source.inclusion_reason,
                    },
                )
        return version

    def update_current_version(self, block: MemoryBlock, version: BlockVersion) -> MemoryBlock:
        with self.connection:
            cursor = self.connection.execute(
                """
                UPDATE memory_blocks
                SET current_version_uuid = ?,
                    value = ?,
                    source_snapshot_hash = ?,
                    build_status = 'current',
                    optimistic_lock_version = optimistic_lock_version + 1,
                    updated_at = ?
                WHERE block_uuid = ? AND optimistic_lock_version = ?
                """,
                (
                    version.block_version_uuid,
                    version.value,
                    version.source_snapshot_hash,
                    utc_now(),
                    block.block_uuid,
                    block.optimistic_lock_version,
                ),
            )
        if cursor.rowcount != 1:
            raise RepositoryError("memory block optimistic lock changed")
        return self.get_block(block.block_uuid)

    def mark_stale(self, block_uuid: str, reason: str) -> None:
        with self.connection:
            self.sqlite.update(
                "memory_blocks",
                {"build_status": "stale", "updated_at": utc_now()},
                "block_uuid = ?",
                (block_uuid,),
            )
            self.connection.execute(
                """
                UPDATE memory_context_attachments
                SET status = 'stale', stale_reason = ?
                WHERE source_block_uuids_ijson LIKE ?
                """,
                (reason, f"%{block_uuid}%"),
            )

    def list_versions(self, block_uuid: str) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in self.connection.execute(
                """
                SELECT *
                FROM memory_block_versions
                WHERE block_uuid = ?
                ORDER BY version_number
                """,
                (block_uuid,),
            )
        ]

    def get_version_by_number(self, block_uuid: str, version_number: int) -> dict[str, Any] | None:
        row = self.connection.execute(
            """
            SELECT *
            FROM memory_block_versions
            WHERE block_uuid = ? AND version_number = ?
            """,
            (block_uuid, version_number),
        ).fetchone()
        return dict(row) if row is not None else None

    def list_sources(self, block_uuid: str) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in self.connection.execute(
                """
                SELECT mbs.*
                FROM memory_block_sources mbs
                JOIN memory_block_versions mbv ON mbv.block_version_uuid = mbs.block_version_uuid
                JOIN memory_blocks mb ON mb.current_version_uuid = mbv.block_version_uuid
                WHERE mb.block_uuid = ?
                ORDER BY mbs.source_role, mbs.memory_version_uuid
                """,
                (block_uuid,),
            )
        ]

    def rollback(self, block_uuid: str, version_number: int) -> dict[str, Any]:
        version = self.get_version_by_number(block_uuid, version_number)
        if version is None:
            raise RepositoryError(f"Block version not found: {block_uuid} v{version_number}")
        with self.connection:
            self.sqlite.update(
                "memory_blocks",
                {
                    "current_version_uuid": version["block_version_uuid"],
                    "value": version["value"],
                    "source_snapshot_hash": version["source_snapshot_hash"],
                    "build_status": "current",
                    "optimistic_lock_version": self.get_block(block_uuid).optimistic_lock_version
                    + 1,
                    "updated_at": utc_now(),
                },
                "block_uuid = ?",
                (block_uuid,),
            )
        return version
