from __future__ import annotations

import sqlite3
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel

from memcore.models import (
    CandidateEvidence,
    ConsolidationOperation,
    GraphProjectionOutboxItem,
    JakobsonAnalysisRun,
    JakobsonAnalysisRunStatus,
    JakobsonSentenceAnnotation,
    LinguisticAnalysis,
    Memory,
    MemoryCandidate,
    MemoryConsolidationDecision,
    MemoryEvidenceLink,
    MemoryGateDecision,
    MemoryProcessingRun,
    MemorySignalRoute,
    MemoryVersion,
    OutboxStatus,
    ProcessingRunStatus,
    TextUnit,
    utc_now,
)
from memcore.repositories.domain import RepositoryError
from memcore.repositories.sqlite import SQLiteRepository
from memcore.validators.ijson import canonical_hash_ijson, dump_ijson
from memcore.validators.payload_policy import prepare_ijson_field


class MemoryProcessingRunRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self.sqlite = SQLiteRepository(connection)

    def get_run(self, processing_run_uuid: str) -> MemoryProcessingRun | None:
        return _fetch_one(
            self.connection,
            MemoryProcessingRun,
            "SELECT * FROM memory_processing_runs WHERE processing_run_uuid = ?",
            (processing_run_uuid,),
        )

    def get_or_create_run(
        self,
        session_uuid: str,
        message_uuid: str,
        pipeline_version: str,
        input_content_hash: str,
        prompt_bundle_version: str | None = None,
        prompt_id: str | None = None,
        prompt_version: str | None = None,
        model_profile_uuid: str | None = None,
        provider_type: str | None = None,
        input_hash: str | None = None,
    ) -> MemoryProcessingRun:
        existing = _fetch_one(
            self.connection,
            MemoryProcessingRun,
            """
            SELECT * FROM memory_processing_runs
            WHERE message_uuid = ? AND pipeline_version = ? AND input_content_hash = ?
            """,
            (message_uuid, pipeline_version, input_content_hash),
        )
        if existing is not None:
            return existing

        run = MemoryProcessingRun(
            session_uuid=session_uuid,
            message_uuid=message_uuid,
            pipeline_version=pipeline_version,
            prompt_bundle_version=prompt_bundle_version,
            prompt_id=prompt_id,
            prompt_version=prompt_version,
            model_profile_uuid=model_profile_uuid,
            provider_type=provider_type,
            input_content_hash=input_content_hash,
            input_hash=input_hash or input_content_hash,
        )
        with self.connection:
            self.sqlite.insert("memory_processing_runs", _dump_model(run))
        return run

    def mark_started(self, processing_run_uuid: str) -> MemoryProcessingRun:
        return self._update_run(
            processing_run_uuid,
            {"status": ProcessingRunStatus.RUNNING.value, "started_at": utc_now()},
        )

    def mark_succeeded(
        self,
        processing_run_uuid: str,
        raw_output: Any = None,
        latency_ms: int | None = None,
    ) -> MemoryProcessingRun:
        values: dict[str, Any] = {
            "status": ProcessingRunStatus.SUCCEEDED.value,
            "completed_at": utc_now(),
        }
        if raw_output is not None:
            values["raw_output_ijson"] = raw_output
            values["output_hash"] = canonical_hash_ijson(raw_output)
        if latency_ms is not None:
            values["latency_ms"] = latency_ms
        return self._update_run(processing_run_uuid, values)

    def mark_failed(
        self,
        processing_run_uuid: str,
        error_text: str,
        validation_errors: Any = None,
        raw_output: Any = None,
    ) -> MemoryProcessingRun:
        values: dict[str, Any] = {
            "status": ProcessingRunStatus.FAILED.value,
            "completed_at": utc_now(),
            "error_text": error_text,
        }
        if validation_errors is not None:
            values["validation_errors_ijson"] = validation_errors
        if raw_output is not None:
            values["raw_output_ijson"] = raw_output
        return self._update_run(processing_run_uuid, values)

    def _update_run(
        self, processing_run_uuid: str, values: Mapping[str, Any]
    ) -> MemoryProcessingRun:
        with self.connection:
            self.sqlite.update(
                "memory_processing_runs",
                values,
                "processing_run_uuid = ?",
                (processing_run_uuid,),
            )
        run = self.get_run(processing_run_uuid)
        if run is None:
            raise RepositoryError(f"Processing run not found: {processing_run_uuid}")
        return run


class TextUnitRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self.sqlite = SQLiteRepository(connection)

    def create_units(self, units: Sequence[TextUnit]) -> list[TextUnit]:
        if not units:
            return []
        message_uuid = units[0].message_uuid
        existing = self.list_units(message_uuid)
        if existing:
            return existing

        with self.connection:
            for unit in units:
                self.sqlite.insert("text_units", _dump_model(unit))
        return self.list_units(message_uuid)

    def list_units(self, message_uuid: str) -> list[TextUnit]:
        return _fetch_all(
            self.connection,
            TextUnit,
            "SELECT * FROM text_units WHERE message_uuid = ? ORDER BY unit_index",
            (message_uuid,),
        )

    def get_unit(self, text_unit_uuid: str) -> TextUnit | None:
        return _fetch_one(
            self.connection,
            TextUnit,
            "SELECT * FROM text_units WHERE text_unit_uuid = ?",
            (text_unit_uuid,),
        )


class GateDecisionRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self.sqlite = SQLiteRepository(connection)

    def create_decision(self, decision: MemoryGateDecision) -> MemoryGateDecision:
        existing = _fetch_one(
            self.connection,
            MemoryGateDecision,
            """
            SELECT * FROM memory_gate_decisions
            WHERE text_unit_uuid = ? AND processing_run_uuid = ?
            """,
            (decision.text_unit_uuid, decision.processing_run_uuid),
        )
        if existing is not None:
            return existing

        with self.connection:
            self.sqlite.insert("memory_gate_decisions", _dump_model(decision))
        return decision

    def list_decisions_for_run(self, processing_run_uuid: str) -> list[MemoryGateDecision]:
        return _fetch_all(
            self.connection,
            MemoryGateDecision,
            """
            SELECT * FROM memory_gate_decisions
            WHERE processing_run_uuid = ?
            ORDER BY created_at, gate_decision_uuid
            """,
            (processing_run_uuid,),
        )

    def list_decisions_for_unit(self, text_unit_uuid: str) -> list[MemoryGateDecision]:
        return _fetch_all(
            self.connection,
            MemoryGateDecision,
            """
            SELECT * FROM memory_gate_decisions
            WHERE text_unit_uuid = ?
            ORDER BY created_at
            """,
            (text_unit_uuid,),
        )


class LinguisticAnalysisRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self.sqlite = SQLiteRepository(connection)

    def create_analysis(self, analysis: LinguisticAnalysis) -> LinguisticAnalysis:
        existing = _fetch_one(
            self.connection,
            LinguisticAnalysis,
            """
            SELECT * FROM linguistic_analyses
            WHERE text_unit_uuid = ? AND processing_run_uuid = ?
            """,
            (analysis.text_unit_uuid, analysis.processing_run_uuid),
        )
        if existing is not None:
            return existing

        with self.connection:
            self.sqlite.insert("linguistic_analyses", _dump_model(analysis))
        return analysis

    def list_for_run(self, processing_run_uuid: str) -> list[LinguisticAnalysis]:
        return _fetch_all(
            self.connection,
            LinguisticAnalysis,
            """
            SELECT * FROM linguistic_analyses
            WHERE processing_run_uuid = ?
            ORDER BY created_at, analysis_uuid
            """,
            (processing_run_uuid,),
        )

    def get_for_unit(
        self,
        text_unit_uuid: str,
        processing_run_uuid: str,
    ) -> LinguisticAnalysis | None:
        return _fetch_one(
            self.connection,
            LinguisticAnalysis,
            """
            SELECT * FROM linguistic_analyses
            WHERE text_unit_uuid = ? AND processing_run_uuid = ?
            """,
            (text_unit_uuid, processing_run_uuid),
        )


class JakobsonAnalysisRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self.sqlite = SQLiteRepository(connection)

    def create_run(self, run: JakobsonAnalysisRun) -> JakobsonAnalysisRun:
        with self.connection:
            self.sqlite.insert("jakobson_analysis_runs", _dump_model(run))
        return run

    def mark_failed(
        self,
        run: JakobsonAnalysisRun,
        warnings: Any,
        raw_output: Any | None = None,
    ) -> JakobsonAnalysisRun:
        values: dict[str, Any] = {
            "status": JakobsonAnalysisRunStatus.FAILED.value,
            "warnings_ijson": _required_ijson("warnings_ijson", warnings),
        }
        if raw_output is not None:
            values["raw_output_ijson"] = raw_output
            values["output_hash"] = canonical_hash_ijson(raw_output)
        with self.connection:
            self.sqlite.update(
                "jakobson_analysis_runs",
                values,
                "analysis_run_uuid = ?",
                (run.analysis_run_uuid,),
            )
        updated = self.get_run(run.analysis_run_uuid)
        if updated is None:
            raise RepositoryError(f"Jakobson analysis run not found: {run.analysis_run_uuid}")
        return updated

    def get_run(self, analysis_run_uuid: str) -> JakobsonAnalysisRun | None:
        return _fetch_one(
            self.connection,
            JakobsonAnalysisRun,
            "SELECT * FROM jakobson_analysis_runs WHERE analysis_run_uuid = ?",
            (analysis_run_uuid,),
        )

    def list_runs_for_message(self, message_uuid: str) -> list[JakobsonAnalysisRun]:
        return _fetch_all(
            self.connection,
            JakobsonAnalysisRun,
            """
            SELECT * FROM jakobson_analysis_runs
            WHERE message_uuid = ?
            ORDER BY created_at, analysis_run_uuid
            """,
            (message_uuid,),
        )

    def create_annotations(
        self,
        annotations: Sequence[JakobsonSentenceAnnotation],
    ) -> list[JakobsonSentenceAnnotation]:
        if not annotations:
            return []
        with self.connection:
            for annotation in annotations:
                existing = _fetch_one(
                    self.connection,
                    JakobsonSentenceAnnotation,
                    """
                    SELECT * FROM jakobson_sentence_annotations
                    WHERE analysis_run_uuid = ? AND unit_uuid = ?
                    """,
                    (annotation.analysis_run_uuid, annotation.unit_uuid),
                )
                if existing is None:
                    self.sqlite.insert("jakobson_sentence_annotations", _dump_model(annotation))
        return self.list_annotations_for_run(annotations[0].analysis_run_uuid)

    def list_annotations_for_run(
        self,
        analysis_run_uuid: str,
    ) -> list[JakobsonSentenceAnnotation]:
        return _fetch_all(
            self.connection,
            JakobsonSentenceAnnotation,
            """
            SELECT * FROM jakobson_sentence_annotations
            WHERE analysis_run_uuid = ?
            ORDER BY sentence_index, annotation_uuid
            """,
            (analysis_run_uuid,),
        )

    def list_annotations_for_message(
        self,
        message_uuid: str,
    ) -> list[JakobsonSentenceAnnotation]:
        return _fetch_all(
            self.connection,
            JakobsonSentenceAnnotation,
            """
            SELECT * FROM jakobson_sentence_annotations
            WHERE message_uuid = ?
            ORDER BY sentence_index, annotation_uuid
            """,
            (message_uuid,),
        )


class MemorySignalRouteRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self.sqlite = SQLiteRepository(connection)

    def create_routes(self, routes: Sequence[MemorySignalRoute]) -> list[MemorySignalRoute]:
        if not routes:
            return []
        with self.connection:
            for route in routes:
                existing = _fetch_one(
                    self.connection,
                    MemorySignalRoute,
                    """
                    SELECT * FROM memory_signal_routes
                    WHERE annotation_uuid = ? AND route_type = ? AND extractor_id = ?
                    """,
                    (route.annotation_uuid, route.route_type.value, route.extractor_id),
                )
                if existing is None:
                    self.sqlite.insert("memory_signal_routes", _dump_model(route))
        return self.list_for_annotation(routes[0].annotation_uuid)

    def list_for_annotation(self, annotation_uuid: str) -> list[MemorySignalRoute]:
        return _fetch_all(
            self.connection,
            MemorySignalRoute,
            """
            SELECT * FROM memory_signal_routes
            WHERE annotation_uuid = ?
            ORDER BY priority DESC, created_at, route_uuid
            """,
            (annotation_uuid,),
        )

    def list_for_message(self, message_uuid: str) -> list[MemorySignalRoute]:
        return _fetch_all(
            self.connection,
            MemorySignalRoute,
            """
            SELECT * FROM memory_signal_routes
            WHERE message_uuid = ?
            ORDER BY priority DESC, created_at, route_uuid
            """,
            (message_uuid,),
        )


class MemoryCandidateRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self.sqlite = SQLiteRepository(connection)

    def create_candidate(
        self,
        candidate: MemoryCandidate,
        evidence_items: Sequence[CandidateEvidence],
    ) -> MemoryCandidate:
        if not evidence_items:
            raise RepositoryError("memory candidate requires at least one evidence span")
        for evidence in evidence_items:
            self._validate_evidence_span(evidence)

        existing = _fetch_one(
            self.connection,
            MemoryCandidate,
            """
            SELECT * FROM memory_candidates
            WHERE processing_run_uuid = ?
              AND text_unit_uuid = ?
              AND candidate_type = ?
              AND normalized_text = ?
            """,
            (
                candidate.processing_run_uuid,
                candidate.text_unit_uuid,
                candidate.candidate_type.value,
                candidate.normalized_text,
            ),
        )
        if existing is not None:
            return existing

        with self.connection:
            self.sqlite.insert("memory_candidates", _dump_model(candidate))
            for evidence in evidence_items:
                self.sqlite.insert("candidate_evidence", _dump_model(evidence))
        return candidate

    def get_candidate(self, candidate_uuid: str) -> MemoryCandidate | None:
        return _fetch_one(
            self.connection,
            MemoryCandidate,
            "SELECT * FROM memory_candidates WHERE candidate_uuid = ?",
            (candidate_uuid,),
        )

    def list_for_run(self, processing_run_uuid: str) -> list[MemoryCandidate]:
        return _fetch_all(
            self.connection,
            MemoryCandidate,
            """
            SELECT * FROM memory_candidates
            WHERE processing_run_uuid = ?
            ORDER BY created_at, candidate_uuid
            """,
            (processing_run_uuid,),
        )

    def list_evidence(self, candidate_uuid: str) -> list[CandidateEvidence]:
        return _fetch_all(
            self.connection,
            CandidateEvidence,
            """
            SELECT * FROM candidate_evidence
            WHERE candidate_uuid = ?
            ORDER BY created_at, evidence_uuid
            """,
            (candidate_uuid,),
        )

    def _validate_evidence_span(self, evidence: CandidateEvidence) -> None:
        row = self.connection.execute(
            """
            SELECT m.raw_text
            FROM messages m
            WHERE m.message_uuid = ?
            """,
            (evidence.message_uuid,),
        ).fetchone()
        if row is None:
            raise RepositoryError(f"Evidence message not found: {evidence.message_uuid}")
        source_text = str(row["raw_text"] or "")
        if source_text[evidence.start_char : evidence.end_char] != evidence.evidence_text:
            raise RepositoryError("evidence_text must exactly match source message substring")


class MemoryStoreRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self.sqlite = SQLiteRepository(connection)

    def get_memory(self, memory_uuid: str) -> Memory | None:
        return _fetch_one(
            self.connection,
            Memory,
            "SELECT * FROM memories WHERE memory_uuid = ?",
            (memory_uuid,),
        )

    def list_memories(self) -> list[Memory]:
        return _fetch_all(
            self.connection,
            Memory,
            "SELECT * FROM memories ORDER BY created_at, memory_uuid",
        )

    def find_by_canonical_key(
        self,
        scope_type: str,
        scope_uuid: str | None,
        canonical_key: str,
    ) -> Memory | None:
        return _fetch_one(
            self.connection,
            Memory,
            """
            SELECT * FROM memories
            WHERE scope_type = ?
              AND COALESCE(scope_uuid, '') = COALESCE(?, '')
              AND canonical_key = ?
            """,
            (scope_type, scope_uuid, canonical_key),
        )

    def list_versions(self, memory_uuid: str) -> list[MemoryVersion]:
        return _fetch_all(
            self.connection,
            MemoryVersion,
            """
            SELECT * FROM memory_versions
            WHERE memory_uuid = ?
            ORDER BY version_number
            """,
            (memory_uuid,),
        )

    def list_evidence(self, memory_uuid: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT ce.*
            FROM candidate_evidence ce
            JOIN memory_evidence_links mel ON mel.evidence_uuid = ce.evidence_uuid
            WHERE mel.memory_uuid = ?
            ORDER BY ce.created_at, ce.evidence_uuid
            """,
            (memory_uuid,),
        ).fetchall()
        return [dict(row) for row in rows]

    def get_consolidation_decision(
        self,
        candidate_uuid: str,
    ) -> MemoryConsolidationDecision | None:
        return _fetch_one(
            self.connection,
            MemoryConsolidationDecision,
            """
            SELECT * FROM memory_consolidation_decisions
            WHERE candidate_uuid = ?
            """,
            (candidate_uuid,),
        )

    def create_memory_with_version(
        self,
        memory: Memory,
        version: MemoryVersion,
        candidate_uuid: str,
        reason_codes: Sequence[str],
        operation: ConsolidationOperation,
        evidence_items: Sequence[CandidateEvidence],
    ) -> MemoryConsolidationDecision:
        with self.connection:
            self.sqlite.insert("memories", _dump_model(memory))
            self.sqlite.insert("memory_versions", _dump_model(version))
            self.sqlite.update(
                "memories",
                {"current_version_uuid": version.memory_version_uuid, "updated_at": utc_now()},
                "memory_uuid = ?",
                (memory.memory_uuid,),
            )
            decision = self._record_decision(
                candidate_uuid,
                operation,
                reason_codes,
                memory.memory_uuid,
                version.memory_version_uuid,
            )
            self._link_evidence(memory.memory_uuid, version, candidate_uuid, evidence_items)
            self.enqueue_projection(
                "memory",
                memory.memory_uuid,
                operation.value,
                {
                    "memory_uuid": memory.memory_uuid,
                    "memory_version_uuid": version.memory_version_uuid,
                    "operation": operation.value,
                },
            )
        return decision

    def create_version_for_memory(
        self,
        memory: Memory,
        version: MemoryVersion,
        candidate_uuid: str,
        reason_codes: Sequence[str],
        operation: ConsolidationOperation,
        evidence_items: Sequence[CandidateEvidence],
        close_previous: bool = False,
    ) -> MemoryConsolidationDecision:
        now = utc_now()
        with self.connection:
            if close_previous and memory.current_version_uuid is not None:
                self.sqlite.update(
                    "memory_versions",
                    {"transaction_until": now},
                    "memory_version_uuid = ?",
                    (memory.current_version_uuid,),
                )
            self.sqlite.insert("memory_versions", _dump_model(version))
            self.sqlite.update(
                "memories",
                {"current_version_uuid": version.memory_version_uuid, "updated_at": now},
                "memory_uuid = ?",
                (memory.memory_uuid,),
            )
            decision = self._record_decision(
                candidate_uuid,
                operation,
                reason_codes,
                memory.memory_uuid,
                version.memory_version_uuid,
            )
            self._link_evidence(memory.memory_uuid, version, candidate_uuid, evidence_items)
            self.enqueue_projection(
                "memory",
                memory.memory_uuid,
                operation.value,
                {
                    "memory_uuid": memory.memory_uuid,
                    "memory_version_uuid": version.memory_version_uuid,
                    "operation": operation.value,
                },
            )
        return decision

    def record_decision_only(
        self,
        candidate_uuid: str,
        operation: ConsolidationOperation,
        reason_codes: Sequence[str],
    ) -> MemoryConsolidationDecision:
        with self.connection:
            return self._record_decision(candidate_uuid, operation, reason_codes, None, None)

    def record_reinforcement(
        self,
        memory: Memory,
        version: MemoryVersion,
        candidate_uuid: str,
        reason_codes: Sequence[str],
        evidence_items: Sequence[CandidateEvidence],
    ) -> MemoryConsolidationDecision:
        with self.connection:
            decision = self._record_decision(
                candidate_uuid,
                ConsolidationOperation.REINFORCE,
                reason_codes,
                memory.memory_uuid,
                version.memory_version_uuid,
            )
            self._link_evidence(memory.memory_uuid, version, candidate_uuid, evidence_items)
            self.enqueue_projection(
                "memory",
                memory.memory_uuid,
                ConsolidationOperation.REINFORCE.value,
                {
                    "memory_uuid": memory.memory_uuid,
                    "memory_version_uuid": version.memory_version_uuid,
                    "operation": ConsolidationOperation.REINFORCE.value,
                },
            )
        return decision

    def enqueue_projection(
        self,
        aggregate_type: str,
        aggregate_uuid: str,
        operation: str,
        payload: Any,
    ) -> GraphProjectionOutboxItem:
        existing = _fetch_one(
            self.connection,
            GraphProjectionOutboxItem,
            """
            SELECT * FROM graph_projection_outbox
            WHERE aggregate_type = ? AND aggregate_uuid = ? AND operation = ?
            """,
            (aggregate_type, aggregate_uuid, operation),
        )
        if existing is not None:
            return existing

        item = GraphProjectionOutboxItem(
            aggregate_type=aggregate_type,
            aggregate_uuid=aggregate_uuid,
            operation=operation,
            payload_ijson=_required_ijson("payload_ijson", payload),
        )
        self.sqlite.insert("graph_projection_outbox", _dump_model(item))
        return item

    def list_pending_outbox(self, limit: int = 100) -> list[GraphProjectionOutboxItem]:
        return _fetch_all(
            self.connection,
            GraphProjectionOutboxItem,
            """
            SELECT * FROM graph_projection_outbox
            WHERE status IN ('pending', 'retry')
            ORDER BY created_at, outbox_uuid
            LIMIT ?
            """,
            (limit,),
        )

    def mark_outbox_succeeded(self, outbox_uuid: str) -> GraphProjectionOutboxItem:
        return self._update_outbox(
            outbox_uuid,
            {"status": OutboxStatus.SUCCEEDED.value, "processed_at": utc_now()},
        )

    def mark_outbox_retry(self, outbox_uuid: str, error: str) -> GraphProjectionOutboxItem:
        row = self.connection.execute(
            "SELECT attempts FROM graph_projection_outbox WHERE outbox_uuid = ?",
            (outbox_uuid,),
        ).fetchone()
        attempts = int(row["attempts"]) + 1 if row is not None else 1
        return self._update_outbox(
            outbox_uuid,
            {"status": OutboxStatus.RETRY.value, "attempts": attempts, "last_error": error},
        )

    def _update_outbox(
        self,
        outbox_uuid: str,
        values: Mapping[str, Any],
    ) -> GraphProjectionOutboxItem:
        with self.connection:
            self.sqlite.update(
                "graph_projection_outbox",
                values,
                "outbox_uuid = ?",
                (outbox_uuid,),
            )
        item = _fetch_one(
            self.connection,
            GraphProjectionOutboxItem,
            "SELECT * FROM graph_projection_outbox WHERE outbox_uuid = ?",
            (outbox_uuid,),
        )
        if item is None:
            raise RepositoryError(f"Outbox item not found: {outbox_uuid}")
        return item

    def _record_decision(
        self,
        candidate_uuid: str,
        operation: ConsolidationOperation,
        reason_codes: Sequence[str],
        target_memory_uuid: str | None,
        target_memory_version_uuid: str | None,
    ) -> MemoryConsolidationDecision:
        existing = self.get_consolidation_decision(candidate_uuid)
        if existing is not None:
            return existing

        decision = MemoryConsolidationDecision(
            candidate_uuid=candidate_uuid,
            operation=operation,
            resolver_version="deterministic-consolidator-v1",
            reason_codes_ijson=dump_ijson(list(reason_codes)),
            target_memory_uuid=target_memory_uuid,
            target_memory_version_uuid=target_memory_version_uuid,
        )
        self.sqlite.insert("memory_consolidation_decisions", _dump_model(decision))
        return decision

    def _link_evidence(
        self,
        memory_uuid: str,
        version: MemoryVersion,
        candidate_uuid: str,
        evidence_items: Sequence[CandidateEvidence],
    ) -> None:
        for evidence in evidence_items:
            link = MemoryEvidenceLink(
                memory_uuid=memory_uuid,
                memory_version_uuid=version.memory_version_uuid,
                candidate_uuid=candidate_uuid,
                evidence_uuid=evidence.evidence_uuid,
            )
            self.sqlite.insert("memory_evidence_links", _dump_model(link))


def canonical_payload_hash(value: Any) -> str:
    return canonical_hash_ijson(value)


def canonical_text_hash(text: str) -> str:
    return canonical_hash_ijson({"text": text})


def _required_ijson(field_name: str, value: Any) -> str:
    prepared = prepare_ijson_field(field_name, value)
    if prepared is None:
        raise RepositoryError(f"{field_name} is required")
    return prepared


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
