import sqlite3
from typing import Any


class MemoryInspectionService:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def inspect_memory(self, memory_uuid: str) -> dict[str, Any]:
        memory = self.connection.execute(
            "SELECT * FROM memories WHERE memory_uuid = ?",
            (memory_uuid,),
        ).fetchone()
        if memory is None:
            raise ValueError(f"Memory not found: {memory_uuid}")
        versions = [
            dict(row)
            for row in self.connection.execute(
                "SELECT * FROM memory_versions WHERE memory_uuid = ? ORDER BY version_number",
                (memory_uuid,),
            )
        ]
        evidence = [
            dict(row)
            for row in self.connection.execute(
                """
                SELECT ce.*
                FROM candidate_evidence ce
                JOIN memory_evidence_links mel ON mel.evidence_uuid = ce.evidence_uuid
                WHERE mel.memory_uuid = ?
                ORDER BY ce.created_at, ce.evidence_uuid
                """,
                (memory_uuid,),
            )
        ]
        block_dependencies = [
            dict(row)
            for row in self.connection.execute(
                """
                SELECT mb.block_uuid, mb.block_type, mb.label, mbv.version_number
                FROM memory_block_sources mbs
                JOIN memory_block_versions mbv ON mbv.block_version_uuid = mbs.block_version_uuid
                JOIN memory_blocks mb ON mb.block_uuid = mbv.block_uuid
                WHERE mbs.memory_uuid = ?
                ORDER BY mb.label, mbv.version_number
                """,
                (memory_uuid,),
            )
        ]
        usage = [
            dict(row)
            for row in self.connection.execute(
                "SELECT * FROM memory_delivery_events WHERE memory_uuid = ?",
                (memory_uuid,),
            )
        ]
        return {
            "memory": dict(memory),
            "versions": versions,
            "evidence": evidence,
            "block_dependencies": block_dependencies,
            "usage": usage,
        }
