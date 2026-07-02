from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from fastapi import APIRouter, HTTPException

from memcore.config import get_settings
from memcore.memory_worker.graph import GraphProjectionRunner
from memcore.memory_worker.pipeline import MemoryWorkerPipeline
from memcore.repositories import MessageRepository
from memcore.repositories.memory_worker import (
    MemoryCandidateRepository,
    MemoryProcessingRunRepository,
    MemoryStoreRepository,
    TextUnitRepository,
)
from memcore.storage.migrations import apply_migrations
from memcore.storage.sqlite import connect

router = APIRouter(prefix="/memcore", tags=["memory-worker"])


@router.get("/memory-processing/runs/{processing_run_uuid}", response_model=None)
def get_processing_run(processing_run_uuid: str) -> dict[str, Any]:
    with _connection() as connection:
        run = MemoryProcessingRunRepository(connection).get_run(processing_run_uuid)
        if run is None:
            raise HTTPException(status_code=404, detail="processing run not found")
        return run.model_dump(mode="json")


@router.get("/messages/{message_uuid}/memory-lineage", response_model=None)
def get_message_lineage(message_uuid: str) -> dict[str, Any]:
    with _connection() as connection:
        message = MessageRepository(connection).get_message(message_uuid)
        if message is None:
            raise HTTPException(status_code=404, detail="message not found")
        units = TextUnitRepository(connection).list_units(message_uuid)
        runs = _rows(
            connection,
            "SELECT * FROM memory_processing_runs WHERE message_uuid = ? ORDER BY created_at",
            (message_uuid,),
        )
        unit_uuids = [unit.text_unit_uuid for unit in units]
        return {
            "message": message.model_dump(mode="json"),
            "processing_runs": runs,
            "units": [unit.model_dump(mode="json") for unit in units],
            "gate_decisions": _rows_for_uuids(
                connection,
                "memory_gate_decisions",
                "text_unit_uuid",
                unit_uuids,
            ),
            "analyses": _rows_for_uuids(
                connection, "linguistic_analyses", "text_unit_uuid", unit_uuids
            ),
            "candidates": _rows_for_uuids(
                connection, "memory_candidates", "text_unit_uuid", unit_uuids
            ),
            "evidence": _rows_for_uuids(
                connection, "candidate_evidence", "text_unit_uuid", unit_uuids
            ),
            "consolidation_decisions": _rows(
                connection,
                """
                SELECT mcd.*
                FROM memory_consolidation_decisions mcd
                JOIN memory_candidates mc ON mc.candidate_uuid = mcd.candidate_uuid
                WHERE mc.text_unit_uuid IN ({})
                """.format(",".join("?" for _ in unit_uuids) or "NULL"),
                tuple(unit_uuids),
            )
            if unit_uuids
            else [],
            "memory_versions": _rows(
                connection,
                """
                SELECT mv.*
                FROM memory_versions mv
                JOIN memory_candidates mc ON mc.candidate_uuid = mv.source_candidate_uuid
                WHERE mc.text_unit_uuid IN ({})
                """.format(",".join("?" for _ in unit_uuids) or "NULL"),
                tuple(unit_uuids),
            )
            if unit_uuids
            else [],
            "projection_status": _rows(connection, "SELECT * FROM graph_projection_outbox"),
        }


@router.get("/memory-candidates/{candidate_uuid}", response_model=None)
def get_memory_candidate(candidate_uuid: str) -> dict[str, Any]:
    with _connection() as connection:
        repository = MemoryCandidateRepository(connection)
        candidate = repository.get_candidate(candidate_uuid)
        if candidate is None:
            raise HTTPException(status_code=404, detail="candidate not found")
        return {
            "candidate": candidate.model_dump(mode="json"),
            "evidence": [
                item.model_dump(mode="json") for item in repository.list_evidence(candidate_uuid)
            ],
        }


@router.get("/memories", response_model=None)
def list_memories() -> list[dict[str, Any]]:
    with _connection() as connection:
        return [
            memory.model_dump(mode="json")
            for memory in MemoryStoreRepository(connection).list_memories()
        ]


@router.get("/memories/{memory_uuid}", response_model=None)
def get_memory(memory_uuid: str) -> dict[str, Any]:
    with _connection() as connection:
        memory = MemoryStoreRepository(connection).get_memory(memory_uuid)
        if memory is None:
            raise HTTPException(status_code=404, detail="memory not found")
        return memory.model_dump(mode="json")


@router.get("/memories/{memory_uuid}/versions", response_model=None)
def get_memory_versions(memory_uuid: str) -> list[dict[str, Any]]:
    with _connection() as connection:
        return [
            version.model_dump(mode="json")
            for version in MemoryStoreRepository(connection).list_versions(memory_uuid)
        ]


@router.get("/memories/{memory_uuid}/evidence", response_model=None)
def get_memory_evidence(memory_uuid: str) -> list[dict[str, Any]]:
    with _connection() as connection:
        return MemoryStoreRepository(connection).list_evidence(memory_uuid)


@router.post("/memory-worker/process-message/{message_uuid}", response_model=None)
def process_message(message_uuid: str) -> dict[str, object]:
    settings = get_settings()
    with _connection() as connection:
        return MemoryWorkerPipeline(connection, settings).process_message(message_uuid)


@router.post("/graph-projection/run-once", response_model=None)
def run_graph_projection_once() -> dict[str, int]:
    settings = get_settings()
    with _connection() as connection:
        return GraphProjectionRunner(connection, settings).run_once()


@contextmanager
def _connection() -> Iterator[Any]:
    settings = get_settings()
    connection = connect(settings.db_path)
    try:
        apply_migrations(connection)
        yield connection
    finally:
        connection.close()


def _rows(connection: Any, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    return [dict(row) for row in connection.execute(sql, params)]


def _rows_for_uuids(
    connection: Any,
    table_name: str,
    column_name: str,
    uuids: list[str],
) -> list[dict[str, Any]]:
    if not uuids:
        return []
    placeholders = ",".join("?" for _ in uuids)
    return _rows(
        connection,
        f"SELECT * FROM {table_name} WHERE {column_name} IN ({placeholders})",
        tuple(uuids),
    )
