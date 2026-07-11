from __future__ import annotations

import importlib
import json
from collections.abc import Iterator
from contextlib import contextmanager
from hashlib import sha256
from typing import Any

from fastapi import APIRouter, HTTPException

from memcore.config import Settings, get_settings
from memcore.memory_worker.graph import GraphProjectionRunner
from memcore.memory_worker.pipeline import MemoryWorkerPipeline
from memcore.memory_worker.postgres import PostgresMemoryWorkerPipeline
from memcore.models import new_uuid, utc_now
from memcore.repositories import MessageRepository
from memcore.repositories.memory_worker import (
    MemoryCandidateRepository,
    MemoryProcessingRunRepository,
    MemoryStoreRepository,
    TextUnitRepository,
)
from memcore.storage.sqlite import connect

router = APIRouter(prefix="/memcore", tags=["memory-worker"])


@router.get("/memory-processing/runs/{processing_run_uuid}", response_model=None)
def get_processing_run(processing_run_uuid: str) -> dict[str, Any]:
    settings = get_settings()
    if _is_full_postgres(settings):
        with _pg_connection(settings) as connection:
            row = connection.execute(
                "SELECT * FROM memory_processing_runs WHERE processing_run_uuid = %s",
                (processing_run_uuid,),
            ).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="processing run not found")
            return _jsonable_dict(row)
    with _connection() as connection:
        run = MemoryProcessingRunRepository(connection).get_run(processing_run_uuid)
        if run is None:
            raise HTTPException(status_code=404, detail="processing run not found")
        return run.model_dump(mode="json")


@router.get("/messages/{message_uuid}/memory-lineage", response_model=None)
def get_message_lineage(message_uuid: str) -> dict[str, Any]:
    settings = get_settings()
    if _is_full_postgres(settings):
        return _pg_message_memory_lineage(settings, message_uuid)
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
    settings = get_settings()
    if _is_full_postgres(settings):
        with _pg_connection(settings) as connection:
            candidate = connection.execute(
                "SELECT * FROM memory_candidates WHERE candidate_uuid = %s",
                (candidate_uuid,),
            ).fetchone()
            if candidate is None:
                raise HTTPException(status_code=404, detail="candidate not found")
            evidence = _pg_rows(
                connection,
                "SELECT * FROM candidate_evidence WHERE candidate_uuid = %s ORDER BY created_at",
                (candidate_uuid,),
            )
            return {"candidate": _jsonable_dict(candidate), "evidence": evidence}
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
    settings = get_settings()
    if _is_full_postgres(settings):
        with _pg_connection(settings) as connection:
            return _pg_rows(
                connection,
                """
                SELECT m.*, mv.memory_version_uuid, mv.value, mv.normalized_text,
                       mv.confidence, mv.importance, mv.status AS version_status
                FROM memories m
                LEFT JOIN memory_versions mv ON mv.memory_version_uuid = m.current_version_uuid
                ORDER BY m.created_at, m.memory_uuid
                """,
            )
    with _connection() as connection:
        return [
            memory.model_dump(mode="json")
            for memory in MemoryStoreRepository(connection).list_memories()
        ]


@router.get("/memories/{memory_uuid}", response_model=None)
def get_memory(memory_uuid: str) -> dict[str, Any]:
    settings = get_settings()
    if _is_full_postgres(settings):
        with _pg_connection(settings) as connection:
            memory = connection.execute(
                "SELECT * FROM memories WHERE memory_uuid = %s",
                (memory_uuid,),
            ).fetchone()
            if memory is None:
                raise HTTPException(status_code=404, detail="memory not found")
            return _jsonable_dict(memory)
    with _connection() as connection:
        memory = MemoryStoreRepository(connection).get_memory(memory_uuid)
        if memory is None:
            raise HTTPException(status_code=404, detail="memory not found")
        return memory.model_dump(mode="json")


@router.get("/memories/{memory_uuid}/versions", response_model=None)
def get_memory_versions(memory_uuid: str) -> list[dict[str, Any]]:
    settings = get_settings()
    if _is_full_postgres(settings):
        with _pg_connection(settings) as connection:
            return _pg_rows(
                connection,
                "SELECT * FROM memory_versions WHERE memory_uuid = %s ORDER BY version_number",
                (memory_uuid,),
            )
    with _connection() as connection:
        return [
            version.model_dump(mode="json")
            for version in MemoryStoreRepository(connection).list_versions(memory_uuid)
        ]


@router.get("/memories/{memory_uuid}/evidence", response_model=None)
def get_memory_evidence(memory_uuid: str) -> list[dict[str, Any]]:
    settings = get_settings()
    if _is_full_postgres(settings):
        with _pg_connection(settings) as connection:
            return _pg_rows(
                connection,
                """
                SELECT mel.*, ce.evidence_text, ce.message_uuid, ce.text_unit_uuid
                FROM memory_evidence_links mel
                JOIN candidate_evidence ce ON ce.evidence_uuid = mel.evidence_uuid
                WHERE mel.memory_uuid = %s
                ORDER BY mel.created_at
                """,
                (memory_uuid,),
            )
    with _connection() as connection:
        return MemoryStoreRepository(connection).list_evidence(memory_uuid)


@router.post("/memory-worker/process-message/{message_uuid}", response_model=None)
def process_message(message_uuid: str) -> dict[str, object]:
    settings = get_settings()
    if _is_full_postgres(settings):
        with _pg_connection(settings) as connection, connection.transaction():
            return PostgresMemoryWorkerPipeline(connection, settings).process_message(message_uuid)
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
        yield connection
    finally:
        connection.close()


def _is_full_postgres(settings: Settings) -> bool:
    return settings.runtime_profile == "full" and settings.canonical_store == "postgres"


@contextmanager
def _pg_connection(settings: Settings) -> Iterator[Any]:
    if not settings.postgres_dsn:
        raise RuntimeError("postgres_dsn is required for Full Mode memory routes")
    psycopg = importlib.import_module("psycopg")
    rows = importlib.import_module("psycopg.rows")
    connection = psycopg.connect(settings.postgres_dsn, row_factory=rows.dict_row)
    try:
        yield connection
    finally:
        connection.close()


def _pg_process_message_smoke(settings: Settings, message_uuid: str) -> dict[str, object]:
    with _pg_connection(settings) as connection:  # noqa: SIM117
        with connection.transaction():
            message = connection.execute(
                "SELECT * FROM messages WHERE message_uuid = %s",
                (message_uuid,),
            ).fetchone()
            if message is None:
                raise HTTPException(status_code=404, detail="message not found")
            existing_run = connection.execute(
                """
                SELECT * FROM memory_processing_runs
                WHERE message_uuid = %s AND pipeline_version = 'full-smoke-v1'
                ORDER BY created_at
                LIMIT 1
                """,
                (message_uuid,),
            ).fetchone()
            if existing_run is not None:
                return _pg_process_summary(
                    connection,
                    message_uuid,
                    str(existing_run["processing_run_uuid"]),
                    True,
                )

            raw_text = str(message.get("raw_text") or "").strip()
            if not raw_text:
                raise HTTPException(status_code=400, detail="message has no raw_text to process")
            now = utc_now()
            content_hash = sha256(raw_text.encode("utf-8")).hexdigest()
            processing_run_uuid = new_uuid()
            connection.execute(
                """
                INSERT INTO memory_processing_runs (
                    processing_run_uuid, session_uuid, message_uuid, pipeline_version,
                    prompt_bundle_version, input_content_hash, status, started_at,
                    finished_at, error_sanitized, created_at, schema_version
                )
                VALUES (%s, %s, %s, 'full-smoke-v1', 'deterministic-full-smoke', %s,
                        'succeeded', %s, %s, NULL, %s, 1)
                """,
                (
                    processing_run_uuid,
                    message["session_uuid"],
                    message_uuid,
                    content_hash,
                    now,
                    now,
                    now,
                ),
            )
            text_unit_uuid = new_uuid()
            connection.execute(
                """
                INSERT INTO text_units (
                    text_unit_uuid, unit_uuid, message_uuid, session_uuid, speaker_role,
                    unit_type, unit_index, text, start_char, end_char, char_start,
                    char_end, language_code, language_hint, segmentation_confidence,
                    segmentation_notes, content_hash, created_at, schema_version
                )
                VALUES (%s, %s, %s, %s, %s, 'sentence', 0, %s, 0, %s, 0, %s,
                        NULL, NULL, 'deterministic', 'full-smoke single-unit segmentation',
                        %s, %s, 1)
                ON CONFLICT (message_uuid, unit_type, unit_index) DO NOTHING
                """,
                (
                    text_unit_uuid,
                    text_unit_uuid,
                    message_uuid,
                    message["session_uuid"],
                    message["role"],
                    raw_text,
                    len(raw_text),
                    len(raw_text),
                    content_hash,
                    now,
                ),
            )
            unit = connection.execute(
                """
                SELECT * FROM text_units
                WHERE message_uuid = %s AND unit_type = 'sentence' AND unit_index = 0
                """,
                (message_uuid,),
            ).fetchone()
            text_unit_uuid = str(unit["text_unit_uuid"])
            candidate_uuid = new_uuid()
            object_text = json.dumps({"text": raw_text}, ensure_ascii=False, sort_keys=True)
            connection.execute(
                """
                INSERT INTO memory_candidates (
                    candidate_uuid, processing_run_uuid, text_unit_uuid, candidate_type,
                    subject_key, predicate, object_jsonb, normalized_text, source_authority,
                    explicitness, confidence, importance, sensitivity, status,
                    rejection_reason, extraction_metadata_jsonb, created_at, schema_version
                )
                VALUES (%s, %s, %s, 'observation', %s, 'states', %s::jsonb, %s,
                        'user_message', 'explicit', 0.70, 0.50, 'normal', 'accepted',
                        NULL, %s::jsonb, %s, 1)
                """,
                (
                    candidate_uuid,
                    processing_run_uuid,
                    text_unit_uuid,
                    f"message:{message_uuid}",
                    object_text,
                    raw_text,
                    json.dumps({"pipeline": "full-smoke-v1"}, sort_keys=True),
                    now,
                ),
            )
            evidence_uuid = new_uuid()
            connection.execute(
                """
                INSERT INTO candidate_evidence (
                    evidence_uuid, candidate_uuid, message_uuid, text_unit_uuid,
                    annotation_uuid, route_uuid, evidence_text, start_char, end_char,
                    created_at, schema_version
                )
                VALUES (%s, %s, %s, %s, NULL, NULL, %s, 0, %s, %s, 1)
                """,
                (
                    evidence_uuid,
                    candidate_uuid,
                    message_uuid,
                    text_unit_uuid,
                    raw_text,
                    len(raw_text),
                    now,
                ),
            )
            memory_uuid = new_uuid()
            memory_version_uuid = new_uuid()
            canonical_key = f"message:{message_uuid}:observation"
            connection.execute(
                """
                INSERT INTO memories (
                    memory_uuid, scope_type, scope_uuid, canonical_key, current_version_uuid,
                    status, created_at, updated_at, schema_version
                )
                VALUES (%s, 'session', %s, %s, %s, 'active', %s, %s, 1)
                """,
                (
                    memory_uuid,
                    message["session_uuid"],
                    canonical_key,
                    memory_version_uuid,
                    now,
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO memory_versions (
                    memory_version_uuid, memory_uuid, version_number, operation, value,
                    normalized_text, confidence, importance, source_snapshot_hash,
                    transaction_from, transaction_until, valid_from, valid_until,
                    status, created_at, schema_version
                )
                VALUES (%s, %s, 1, 'create', %s, %s, 0.70, 0.50, %s,
                        %s, NULL, %s, NULL, 'current', %s, 1)
                """,
                (memory_version_uuid, memory_uuid, raw_text, raw_text, content_hash, now, now, now),
            )
            connection.execute(
                """
                INSERT INTO memory_evidence_links (
                    link_uuid, memory_uuid, memory_version_uuid, candidate_uuid,
                    evidence_uuid, created_at, schema_version
                )
                VALUES (%s, %s, %s, %s, %s, %s, 1)
                """,
                (new_uuid(), memory_uuid, memory_version_uuid, candidate_uuid, evidence_uuid, now),
            )
            outbox_payload = json.dumps(
                {"memory_uuid": memory_uuid, "memory_version_uuid": memory_version_uuid},
                sort_keys=True,
            )
            connection.execute(
                """
                INSERT INTO graph_projection_outbox (
                    outbox_uuid, event_type, source_type, source_uuid, payload_jsonb,
                    status, priority, attempts, run_after, created_at, updated_at, schema_version
                )
                VALUES (%s, 'memory_upserted', 'memory', %s, %s::jsonb,
                        'pending', 50, 0, %s, %s, %s, 1)
                ON CONFLICT (event_type, source_type, source_uuid) DO NOTHING
                """,
                (new_uuid(), memory_uuid, outbox_payload, now, now, now),
            )
            return _pg_process_summary(connection, message_uuid, processing_run_uuid, False)


def _pg_process_summary(
    connection: Any,
    message_uuid: str,
    processing_run_uuid: str,
    idempotent_replay: bool,
) -> dict[str, object]:
    return {
        "message_uuid": message_uuid,
        "processing_run_uuid": processing_run_uuid,
        "pipeline_version": "full-smoke-v1",
        "idempotent_replay": idempotent_replay,
        "text_units": _pg_count(connection, "text_units", "message_uuid", message_uuid),
        "memory_candidates": _pg_count(
            connection,
            "memory_candidates",
            "processing_run_uuid",
            processing_run_uuid,
        ),
        "memories": _pg_count_memories_for_run(connection, processing_run_uuid),
        "status": "succeeded",
    }


def _pg_message_memory_lineage(settings: Settings, message_uuid: str) -> dict[str, Any]:
    with _pg_connection(settings) as connection:
        message = connection.execute(
            "SELECT * FROM messages WHERE message_uuid = %s",
            (message_uuid,),
        ).fetchone()
        if message is None:
            raise HTTPException(status_code=404, detail="message not found")
        units = _pg_rows(
            connection,
            "SELECT * FROM text_units WHERE message_uuid = %s ORDER BY unit_index",
            (message_uuid,),
        )
        unit_uuids = [str(row["text_unit_uuid"]) for row in units]
        runs = _pg_rows(
            connection,
            "SELECT * FROM memory_processing_runs WHERE message_uuid = %s ORDER BY created_at",
            (message_uuid,),
        )
        candidates = _pg_rows_for_values(
            connection,
            "memory_candidates",
            "text_unit_uuid",
            unit_uuids,
        )
        candidate_uuids = [str(row["candidate_uuid"]) for row in candidates]
        return {
            "message": _jsonable_dict(message),
            "processing_runs": runs,
            "units": units,
            "gate_decisions": [],
            "analyses": [],
            "candidates": candidates,
            "evidence": _pg_rows_for_values(
                connection,
                "candidate_evidence",
                "candidate_uuid",
                candidate_uuids,
            ),
            "memory_versions": _pg_memory_versions_for_candidates(connection, candidate_uuids),
            "projection_status": _pg_rows(connection, "SELECT * FROM graph_projection_outbox"),
        }


def _pg_memory_versions_for_candidates(
    connection: Any,
    candidate_uuids: list[str],
) -> list[dict[str, Any]]:
    if not candidate_uuids:
        return []
    placeholders = ",".join("%s" for _ in candidate_uuids)
    return _pg_rows(
        connection,
        f"""
        SELECT mv.*
        FROM memory_versions mv
        JOIN memory_evidence_links mel ON mel.memory_version_uuid = mv.memory_version_uuid
        WHERE mel.candidate_uuid IN ({placeholders})
        ORDER BY mv.created_at
        """,
        tuple(candidate_uuids),
    )


def _pg_count(connection: Any, table_name: str, column_name: str, value: str) -> int:
    row = connection.execute(
        f"SELECT count(*) AS count FROM {table_name} WHERE {column_name} = %s",
        (value,),
    ).fetchone()
    return int(row["count"])


def _pg_count_memories_for_run(connection: Any, processing_run_uuid: str) -> int:
    row = connection.execute(
        """
        SELECT count(DISTINCT mel.memory_uuid) AS count
        FROM memory_evidence_links mel
        JOIN memory_candidates mc ON mc.candidate_uuid = mel.candidate_uuid
        WHERE mc.processing_run_uuid = %s
        """,
        (processing_run_uuid,),
    ).fetchone()
    return int(row["count"])


def _pg_rows(connection: Any, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    return [_jsonable_dict(row) for row in connection.execute(sql, params).fetchall()]


def _pg_rows_for_values(
    connection: Any,
    table_name: str,
    column_name: str,
    values: list[str],
) -> list[dict[str, Any]]:
    if not values:
        return []
    placeholders = ",".join("%s" for _ in values)
    return _pg_rows(
        connection,
        f"SELECT * FROM {table_name} WHERE {column_name} IN ({placeholders})",
        tuple(values),
    )


def _jsonable_dict(row: Any) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in dict(row).items():
        if hasattr(value, "isoformat"):
            result[str(key)] = value.isoformat()
        else:
            result[str(key)] = value
    return result


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
