from __future__ import annotations

import sqlite3
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel

from memcore.models import (
    MemoryVersionEmbedding,
    RetrievalCandidate,
    RetrievalQuery,
    RetrievalRun,
    RetrievalRunStatus,
    utc_now,
)
from memcore.repositories.domain import RepositoryError
from memcore.repositories.sqlite import SQLiteRepository
from memcore.validators.ijson import dump_ijson


class RetrievalRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self.sqlite = SQLiteRepository(connection)

    def create_run(self, run: RetrievalRun) -> RetrievalRun:
        with self.connection:
            self.sqlite.insert("retrieval_runs", _dump_model(run))
        return run

    def get_run(self, retrieval_run_uuid: str) -> RetrievalRun | None:
        return _fetch_one(
            self.connection,
            RetrievalRun,
            "SELECT * FROM retrieval_runs WHERE retrieval_run_uuid = ?",
            (retrieval_run_uuid,),
        )

    def mark_run_started(self, retrieval_run_uuid: str) -> RetrievalRun:
        return self.update_run(
            retrieval_run_uuid,
            {"status": RetrievalRunStatus.RUNNING.value, "started_at": utc_now()},
        )

    def mark_run_completed(
        self,
        retrieval_run_uuid: str,
        latency_ms: int | None = None,
    ) -> RetrievalRun:
        values: dict[str, Any] = {
            "status": RetrievalRunStatus.COMPLETED.value,
            "completed_at": utc_now(),
        }
        if latency_ms is not None:
            values["latency_ms"] = latency_ms
        return self.update_run(retrieval_run_uuid, values)

    def mark_run_abstained(
        self,
        retrieval_run_uuid: str,
        reason: str,
        latency_ms: int | None = None,
    ) -> RetrievalRun:
        values: dict[str, Any] = {
            "status": RetrievalRunStatus.ABSTAINED.value,
            "abstention_status": "abstained",
            "abstention_reason": reason,
            "completed_at": utc_now(),
        }
        if latency_ms is not None:
            values["latency_ms"] = latency_ms
        return self.update_run(retrieval_run_uuid, values)

    def mark_run_failed(self, retrieval_run_uuid: str, reason: str) -> RetrievalRun:
        return self.update_run(
            retrieval_run_uuid,
            {
                "status": RetrievalRunStatus.FAILED.value,
                "abstention_status": "failed",
                "abstention_reason": reason,
                "completed_at": utc_now(),
            },
        )

    def update_run(self, retrieval_run_uuid: str, values: Mapping[str, Any]) -> RetrievalRun:
        with self.connection:
            self.sqlite.update(
                "retrieval_runs",
                values,
                "retrieval_run_uuid = ?",
                (retrieval_run_uuid,),
            )
        run = self.get_run(retrieval_run_uuid)
        if run is None:
            raise RepositoryError(f"Retrieval run not found: {retrieval_run_uuid}")
        return run

    def create_queries(self, queries: Sequence[RetrievalQuery]) -> list[RetrievalQuery]:
        if not queries:
            return []
        retrieval_run_uuid = queries[0].retrieval_run_uuid
        existing = self.list_queries(retrieval_run_uuid)
        if existing:
            return existing
        with self.connection:
            for query in queries:
                self.sqlite.insert("retrieval_queries", _dump_model(query))
        return self.list_queries(retrieval_run_uuid)

    def list_queries(self, retrieval_run_uuid: str) -> list[RetrievalQuery]:
        return _fetch_all(
            self.connection,
            RetrievalQuery,
            """
            SELECT * FROM retrieval_queries
            WHERE retrieval_run_uuid = ?
            ORDER BY query_index
            """,
            (retrieval_run_uuid,),
        )

    def upsert_candidates(
        self,
        candidates: Sequence[RetrievalCandidate],
    ) -> list[RetrievalCandidate]:
        if not candidates:
            return []
        with self.connection:
            for candidate in candidates:
                existing = _fetch_one(
                    self.connection,
                    RetrievalCandidate,
                    """
                    SELECT * FROM retrieval_candidates
                    WHERE retrieval_run_uuid = ?
                      AND memory_version_uuid = ?
                      AND generator_type = ?
                    """,
                    (
                        candidate.retrieval_run_uuid,
                        candidate.memory_version_uuid,
                        candidate.generator_type,
                    ),
                )
                if existing is None:
                    self.sqlite.insert("retrieval_candidates", _dump_model(candidate))
        return self.list_candidates(candidates[0].retrieval_run_uuid)

    def list_candidates(self, retrieval_run_uuid: str) -> list[RetrievalCandidate]:
        return _fetch_all(
            self.connection,
            RetrievalCandidate,
            """
            SELECT * FROM retrieval_candidates
            WHERE retrieval_run_uuid = ?
            ORDER BY COALESCE(final_score, fused_score, 0) DESC, created_at
            """,
            (retrieval_run_uuid,),
        )

    def update_candidate_scores(
        self,
        retrieval_candidate_uuid: str,
        values: Mapping[str, Any],
    ) -> RetrievalCandidate:
        with self.connection:
            self.sqlite.update(
                "retrieval_candidates",
                values,
                "retrieval_candidate_uuid = ?",
                (retrieval_candidate_uuid,),
            )
        candidate = _fetch_one(
            self.connection,
            RetrievalCandidate,
            "SELECT * FROM retrieval_candidates WHERE retrieval_candidate_uuid = ?",
            (retrieval_candidate_uuid,),
        )
        if candidate is None:
            raise RepositoryError(f"Retrieval candidate not found: {retrieval_candidate_uuid}")
        return candidate

    def upsert_embedding(self, embedding: MemoryVersionEmbedding) -> MemoryVersionEmbedding:
        values = _dump_model(embedding)
        columns = ", ".join(values)
        placeholders = ", ".join("?" for _ in values)
        updates = ", ".join(
            f"{column} = excluded.{column}"
            for column in values
            if column not in {"memory_version_uuid", "embedding_model", "embedding_version"}
        )
        with self.connection:
            self.connection.execute(
                f"""
                INSERT INTO memory_version_embeddings ({columns})
                VALUES ({placeholders})
                ON CONFLICT(memory_version_uuid, embedding_model, embedding_version)
                DO UPDATE SET {updates}
                """,
                tuple(values.values()),
            )
        return embedding

    def record_preflight_event(
        self,
        event_type: str,
        payload: Any,
        session_uuid: str | None = None,
        input_message_uuid: str | None = None,
        attachment_uuid: str | None = None,
        retrieval_run_uuid: str | None = None,
    ) -> None:
        with self.connection:
            self.sqlite.insert(
                "preflight_events",
                {
                    "preflight_event_uuid": _uuid_from_sqlite(self.connection),
                    "session_uuid": session_uuid,
                    "input_message_uuid": input_message_uuid,
                    "attachment_uuid": attachment_uuid,
                    "retrieval_run_uuid": retrieval_run_uuid,
                    "event_type": event_type,
                    "payload_ijson": dump_ijson(payload),
                    "created_at": utc_now(),
                    "schema_version": 1,
                },
            )

    def get_assistant_response_link(
        self,
        input_message_uuid: str,
        content_hash: str,
        provider_response_id: str | None = None,
    ) -> dict[str, Any] | None:
        row = self.connection.execute(
            """
            SELECT *
            FROM assistant_response_links
            WHERE input_message_uuid = ?
              AND (
                    (provider_response_id IS NOT NULL AND provider_response_id = ?)
                 OR (? IS NULL AND content_hash = ?)
              )
            """,
            (input_message_uuid, provider_response_id, provider_response_id, content_hash),
        ).fetchone()
        return dict(row) if row is not None else None

    def record_assistant_response_link(
        self,
        input_message_uuid: str,
        assistant_message_uuid: str,
        content_hash: str,
        attachment_uuid: str | None = None,
        provider_response_id: str | None = None,
    ) -> dict[str, Any]:
        existing = self.get_assistant_response_link(
            input_message_uuid,
            content_hash,
            provider_response_id,
        )
        if existing is not None:
            return existing
        link = {
            "response_link_uuid": _uuid_from_sqlite(self.connection),
            "input_message_uuid": input_message_uuid,
            "assistant_message_uuid": assistant_message_uuid,
            "attachment_uuid": attachment_uuid,
            "provider_response_id": provider_response_id,
            "content_hash": content_hash,
            "created_at": utc_now(),
            "schema_version": 1,
        }
        with self.connection:
            self.sqlite.insert("assistant_response_links", link)
        return link


def _uuid_from_sqlite(connection: sqlite3.Connection) -> str:
    value = connection.execute("SELECT lower(hex(randomblob(16)))").fetchone()[0]
    return f"{value[0:8]}-{value[8:12]}-{value[12:16]}-{value[16:20]}-{value[20:32]}"


def _dump_model(model: BaseModel) -> dict[str, Any]:
    return model.model_dump(mode="json")


def _fetch_one[ModelT: BaseModel](
    connection: sqlite3.Connection,
    model_type: type[ModelT],
    sql: str,
    params: tuple[Any, ...] = (),
) -> ModelT | None:
    row = connection.execute(sql, params).fetchone()
    if row is None:
        return None
    return model_type.model_validate(dict(row))


def _fetch_all[ModelT: BaseModel](
    connection: sqlite3.Connection,
    model_type: type[ModelT],
    sql: str,
    params: tuple[Any, ...] = (),
) -> list[ModelT]:
    return [model_type.model_validate(dict(row)) for row in connection.execute(sql, params)]
