"""SQLite/PostgreSQL mechanics for the shared WP02 orchestration service."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from collections import defaultdict
from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from typing import Any

from memcore.memory_worker.attempt_audit import stable_stage_execution_uuid
from memcore.memory_worker.execution import ContractExecutionOutcome
from memcore.memory_worker.extraction.sensitivity import classify_sensitivity
from memcore.memory_worker.message_semantics import persist_message_semantics
from memcore.memory_worker.postgres.semantic_coverage import (
    PostgresSemanticCoverageRepository,
)
from memcore.memory_worker.prompts.contracts import SemanticAnalysisV1Output
from memcore.memory_worker.prompts.versions import (
    SEMANTIC_CANDIDATE_ANALYSIS_PROMPT_ID,
    SEMANTIC_CANDIDATE_ANALYSIS_VERSION,
)
from memcore.memory_worker.semantic.bounded_context import (
    CurrentContextScope,
    PriorContextRecord,
)
from memcore.memory_worker.semantic.coverage import (
    CandidateProposal,
    CoveragePlan,
    PersistedUnitAuthority,
)
from memcore.memory_worker.semantic.orchestration import (
    RecordedSemanticExecution,
    RecordedSemanticPlanningReplay,
)
from memcore.memory_worker.semantic_coverage_persistence import (
    CandidateAuthorityBinding,
    CoveragePersistenceBindings,
)
from memcore.models import (
    CandidateEvidence,
    MemoryCandidate,
    ModelRole,
    SensitivityClass,
    utc_now,
)
from memcore.repositories.semantic_coverage import SQLiteSemanticCoverageRepository
from memcore.validators.ijson import canonical_hash_ijson, dump_ijson, load_ijson

_SEMANTIC_STAGE = "semantic_candidate_analysis"
_SEMANTIC_TEXT_UNIT_NAMESPACE = uuid.UUID("4c5e2229-f900-5b05-8d7a-48b0b555c457")


class SQLiteSemanticCandidateRuntimeAdapter:
    postgres = False

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self.coverage = SQLiteSemanticCoverageRepository(connection)

    def load_current_context_scope(self, message_uuid: str) -> CurrentContextScope:
        row = self.connection.execute(
            """
            WITH latest_version AS (
              SELECT message_version_uuid, message_uuid, raw_text,
                     ROW_NUMBER() OVER (
                       PARTITION BY message_uuid
                       ORDER BY version_number DESC, message_version_uuid DESC
                     ) AS position
              FROM message_versions
            )
            SELECT m.message_uuid, m.session_uuid, m.role, m.turn_index, m.raw_text,
                   m.visibility, m.is_deleted, m.redaction_status,
                   s.workspace_uuid, s.project_uuid,
                   actor.user_uuid, actor.workspace_uuid AS actor_workspace_uuid,
                   version.message_version_uuid,
                   version.raw_text AS version_raw_text
            FROM messages m
            JOIN sessions s ON s.session_uuid = m.session_uuid
            LEFT JOIN memorist_session_actors actor
              ON actor.session_uuid = m.session_uuid
            LEFT JOIN latest_version version
              ON version.message_uuid = m.message_uuid
             AND version.position = 1
            WHERE m.message_uuid = ?
            """,
            (message_uuid,),
        ).fetchone()
        if row is None:
            raise ValueError(f"message not found: {message_uuid}")
        raw_text = str(row["raw_text"] or "")
        version_uuid = (
            str(row["message_version_uuid"])
            if row["message_version_uuid"] is not None
            and (row["version_raw_text"] is None or str(row["version_raw_text"]) == raw_text)
            else None
        )
        return CurrentContextScope(
            message_uuid=str(row["message_uuid"]),
            message_version_uuid=version_uuid,
            session_uuid=str(row["session_uuid"]),
            workspace_uuid=_optional_string(row["workspace_uuid"]),
            project_uuid=_optional_string(row["project_uuid"]),
            user_uuid=_optional_string(row["user_uuid"]),
            actor_workspace_uuid=_optional_string(row["actor_workspace_uuid"]),
            role=str(row["role"]),
            turn_index=int(row["turn_index"]) if row["turn_index"] is not None else None,
            raw_text=raw_text,
            visibility=str(row["visibility"]),
            is_deleted=bool(row["is_deleted"]),
            redaction_status=str(row["redaction_status"]),
        )

    def list_prior_context_records(
        self,
        scope: CurrentContextScope,
        *,
        scan_limit: int,
    ) -> Sequence[PriorContextRecord]:
        if scope.turn_index is None:
            return ()
        rows = self.connection.execute(
            """
            WITH latest_version AS (
              SELECT message_version_uuid, message_uuid, raw_text,
                     ROW_NUMBER() OVER (
                       PARTITION BY message_uuid
                       ORDER BY version_number DESC, message_version_uuid DESC
                     ) AS position
              FROM message_versions
            )
            SELECT actor.user_uuid, m.session_uuid, s.workspace_uuid, s.project_uuid,
                   m.message_uuid, version.message_version_uuid,
                   version.raw_text AS version_raw_text, m.role, m.turn_index,
                   m.visibility, m.is_deleted, m.redaction_status,
                   unit.text_unit_uuid, unit.unit_index,
                   unit.start_char AS raw_start, unit.end_char AS raw_end,
                   unit.text AS unit_text
            FROM messages m
            JOIN sessions s ON s.session_uuid = m.session_uuid
            LEFT JOIN memorist_session_actors actor
              ON actor.session_uuid = m.session_uuid
            JOIN text_units unit ON unit.message_uuid = m.message_uuid
            LEFT JOIN latest_version version
              ON version.message_uuid = m.message_uuid
             AND version.position = 1
            WHERE m.session_uuid = ?
              AND m.message_uuid <> ?
              AND m.turn_index < ?
            ORDER BY m.turn_index DESC, unit.unit_index DESC,
                     m.message_uuid DESC, unit.text_unit_uuid DESC
            LIMIT ?
            """,
            (
                scope.session_uuid,
                scope.message_uuid,
                scope.turn_index,
                scan_limit,
            ),
        ).fetchall()
        return tuple(_prior_context_record(row) for row in rows)

    def load_persisted_authorities(
        self,
        *,
        message_uuid: str,
        processing_run_uuid: str,
    ) -> Sequence[PersistedUnitAuthority]:
        rows = self.connection.execute(
            """
            WITH selected_analysis AS (
              SELECT analysis_run_uuid
              FROM jakobson_analysis_runs
              WHERE message_uuid = ?
              ORDER BY created_at DESC, analysis_run_uuid DESC
              LIMIT 1
            )
            SELECT unit.text_unit_uuid, unit.start_char, unit.end_char, unit.text,
                   gate.gate_decision_uuid, gate.decision,
                   annotation.annotation_uuid,
                   route.route_uuid, route.route_type, route.status, route.priority
            FROM text_units unit
            LEFT JOIN memory_gate_decisions gate
              ON gate.text_unit_uuid = unit.text_unit_uuid
             AND gate.processing_run_uuid = ?
            LEFT JOIN jakobson_sentence_annotations annotation
              ON annotation.unit_uuid = unit.text_unit_uuid
             AND annotation.analysis_run_uuid = (
               SELECT analysis_run_uuid FROM selected_analysis
             )
            LEFT JOIN memory_signal_routes route
              ON route.annotation_uuid = annotation.annotation_uuid
            WHERE unit.message_uuid = ?
            ORDER BY unit.unit_index, route.priority DESC, route.route_uuid
            """,
            (message_uuid, processing_run_uuid, message_uuid),
        ).fetchall()
        return _authorities_from_rows(rows)

    def load_completed_semantic_planning(
        self,
        *,
        message_uuid: str,
        processing_run_uuid: str,
        message_version_uuid: str | None,
        raw_text_hash: str,
        semantic_contract_hash: str,
        route_mapping_version: str,
        provenance_policy_version: str,
        privacy_policy_version: str,
        current_authorities: Sequence[PersistedUnitAuthority],
    ) -> RecordedSemanticPlanningReplay | None:
        row = self.connection.execute(
            """
            SELECT coverage.coverage_run_uuid, coverage.plan_ijson,
                   coverage.processing_run_uuid
            FROM semantic_coverage_runs coverage
            WHERE coverage.message_uuid = ?
              AND coverage.raw_text_hash = ?
              AND coverage.semantic_contract_hash = ?
              AND coverage.route_mapping_version = ?
              AND coverage.provenance_policy_version = ?
              AND coverage.privacy_policy_version = ?
              AND (
                coverage.message_version_uuid = ?
                OR (coverage.message_version_uuid IS NULL AND ? IS NULL)
              )
              AND (? IS NOT NULL OR coverage.processing_run_uuid = ?)
            ORDER BY CASE WHEN coverage.processing_run_uuid = ? THEN 0 ELSE 1 END,
                     coverage.created_at DESC, coverage.coverage_run_uuid DESC
            LIMIT 1
            """,
            (
                message_uuid,
                raw_text_hash,
                semantic_contract_hash,
                route_mapping_version,
                provenance_policy_version,
                privacy_policy_version,
                message_version_uuid,
                message_version_uuid,
                message_version_uuid,
                processing_run_uuid,
                processing_run_uuid,
            ),
        ).fetchone()
        if row is None:
            same_identity = self.connection.execute(
                """
                SELECT message_version_uuid, route_mapping_version,
                       provenance_policy_version, privacy_policy_version
                FROM semantic_coverage_runs
                WHERE message_uuid = ?
                  AND raw_text_hash = ?
                  AND semantic_contract_hash = ?
                LIMIT 1
                """,
                (
                    message_uuid,
                    raw_text_hash,
                    semantic_contract_hash,
                ),
            ).fetchone()
            if same_identity is not None:
                if _optional_string(
                    same_identity["message_version_uuid"]
                ) == message_version_uuid and (
                    same_identity["route_mapping_version"] != route_mapping_version
                    or same_identity["provenance_policy_version"] != provenance_policy_version
                    or same_identity["privacy_policy_version"] != privacy_policy_version
                ):
                    raise RuntimeError(
                        "semantic replay policy version changed for frozen proposal identity"
                    )
                raise RuntimeError(
                    "same-text message version conflicts with frozen proposal identity"
                )
            return None
        plan = CoveragePlan.model_validate_json(str(row["plan_ijson"]))
        links = self.connection.execute(
            """
            SELECT item.coverage_item_uuid, item.raw_start, item.raw_end, item.disposition,
                   item.proposal_uuid, link.candidate_uuid, link.state,
                   gate.decision AS gate_decision,
                   route.route_type, route.status AS route_status,
                   candidate.sensitivity_class
            FROM semantic_coverage_items item
            LEFT JOIN semantic_candidate_links link
              ON link.coverage_item_uuid = item.coverage_item_uuid
            LEFT JOIN memory_candidates candidate
              ON candidate.candidate_uuid = link.candidate_uuid
            LEFT JOIN memory_gate_decisions gate
              ON gate.gate_decision_uuid = item.gate_decision_uuid
            LEFT JOIN memory_signal_routes route
              ON route.route_uuid = item.route_uuid
            WHERE item.coverage_run_uuid = ?
            ORDER BY item.raw_start, item.raw_end, item.coverage_item_uuid
            """,
            (str(row["coverage_run_uuid"]),),
        ).fetchall()
        complete = _validate_completed_replay(
            plan,
            links,
            current_authorities,
        )
        if complete is None:
            if str(row["processing_run_uuid"]) != processing_run_uuid:
                raise RuntimeError("canonical cross-run semantic replay is incomplete")
            return None
        return RecordedSemanticPlanningReplay(
            plan=plan,
            candidate_uuids=complete,
            semantic_stage_execution_uuid=None,
        )

    def load_semantic_execution(
        self,
        *,
        stage_execution_uuid: str,
        input_hash: str,
        contract_hash: str,
    ) -> RecordedSemanticExecution | None:
        prompt_uuid = _semantic_prompt_uuid(stage_execution_uuid)
        row = self.connection.execute(
            """
            SELECT prompt.validated_output_ijson, prompt.input_hash,
                   stage.contract_hash, stage.stage_execution_uuid
            FROM prompt_execution_runs prompt
            JOIN processing_stage_runs stage
              ON stage.stage_execution_uuid = ?
            WHERE prompt.prompt_execution_uuid = ?
              AND prompt.prompt_id = ?
              AND prompt.prompt_version = ?
              AND stage.stage = ?
            """,
            (
                stage_execution_uuid,
                prompt_uuid,
                SEMANTIC_CANDIDATE_ANALYSIS_PROMPT_ID,
                SEMANTIC_CANDIDATE_ANALYSIS_VERSION,
                _SEMANTIC_STAGE,
            ),
        ).fetchone()
        return _recorded_execution(row, prompt_uuid, input_hash, contract_hash)

    def record_semantic_execution(
        self,
        *,
        prompt_execution_uuid: str,
        stage_execution_uuid: str,
        processing_run_uuid: str,
        input_payload: Mapping[str, Any],
        outcome: ContractExecutionOutcome,
        profile: Mapping[str, Any],
        message_uuid: str,
        import_run_uuid: str | None,
        job_uuid: str | None,
        contract_hash: str,
        profile_fingerprint: str,
    ) -> None:
        scope = self.load_current_context_scope(message_uuid)
        input_hash = canonical_hash_ijson(input_payload)
        output_hash = canonical_hash_ijson(outcome.output)
        existing = self.load_semantic_execution(
            stage_execution_uuid=stage_execution_uuid,
            input_hash=input_hash,
            contract_hash=contract_hash,
        )
        if existing is not None:
            return
        now = utc_now()
        provider_type = str(
            profile.get("provider_type") or profile.get("provider") or "deterministic"
        )
        model_name = str(profile.get("model_name") or provider_type)
        model_profile_uuid = _optional_string(profile.get("model_profile_uuid"))
        requested_role = str(
            profile.get("requested_role")
            or profile.get("model_role")
            or ModelRole.MEMORY_EXTRACTION.value
        )
        effective_role = str(
            profile.get("effective_role")
            or profile.get("model_role")
            or ModelRole.MEMORY_EXTRACTION.value
        )
        warnings = _safe_codes(outcome.output.get("warnings"))
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            self.connection.execute(
                """
                INSERT INTO prompt_execution_runs (
                  prompt_execution_uuid, prompt_id, prompt_version, stage,
                  model_profile_uuid, model_role, provider_type, model_name,
                  workspace_uuid, project_uuid, session_uuid, message_uuid,
                  import_run_uuid, job_uuid, input_hash, output_hash, input_ref,
                  raw_output_ijson, validated_output_ijson, status,
                  warnings_ijson, error_sanitized, latency_ms, input_tokens,
                  output_tokens, created_at, schema_version
                ) VALUES (
                  ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1
                )
                """,
                (
                    prompt_execution_uuid,
                    SEMANTIC_CANDIDATE_ANALYSIS_PROMPT_ID,
                    SEMANTIC_CANDIDATE_ANALYSIS_VERSION,
                    _SEMANTIC_STAGE,
                    model_profile_uuid,
                    ModelRole.MEMORY_EXTRACTION.value,
                    provider_type,
                    model_name,
                    scope.workspace_uuid,
                    scope.project_uuid,
                    scope.session_uuid,
                    message_uuid,
                    import_run_uuid,
                    job_uuid,
                    input_hash,
                    output_hash,
                    f"message:{message_uuid}:text-envelope:{input_payload['text_envelope']['raw_text_hash']}",
                    dump_ijson(outcome.output),
                    dump_ijson(outcome.output),
                    outcome.status,
                    dump_ijson(warnings),
                    None,
                    outcome.latency_ms,
                    outcome.input_tokens,
                    outcome.output_tokens,
                    now,
                ),
            )
            stage = _stage_values(
                stage_execution_uuid=stage_execution_uuid,
                processing_run_uuid=processing_run_uuid,
                job_uuid=job_uuid,
                message_uuid=message_uuid,
                requested_role=requested_role,
                effective_role=effective_role,
                model_profile_uuid=model_profile_uuid,
                provider_type=provider_type,
                model_name=model_name,
                scope_source=str(profile.get("scope_source") or "runtime_profile"),
                inheritance_source=_optional_string(profile.get("inheritance_source")),
                contract_hash=contract_hash,
                profile_fingerprint=profile_fingerprint,
                input_hash=input_hash,
                output_hash=output_hash,
                outcome=outcome,
                now=now,
            )
            columns = list(stage)
            self.connection.execute(
                f"INSERT INTO processing_stage_runs ({', '.join(columns)}) "
                f"VALUES ({', '.join('?' for _ in columns)})",
                tuple(stage[column] for column in columns),
            )
            persist_message_semantics(
                self.connection,
                postgres=False,
                message_uuid=message_uuid,
                processing_run_uuid=processing_run_uuid,
                prompt_execution_uuid=prompt_execution_uuid,
                stage_execution_uuid=stage_execution_uuid,
                contract_hash=contract_hash,
                scope=scope,
                input_payload=input_payload,
                outcome=outcome,
            )
            self.connection.execute(
                """
                INSERT INTO model_usage_events (
                  usage_uuid, model_profile_uuid, role,
                  input_tokens, output_tokens, created_at, schema_version,
                  stage, provider_type, model_name, workspace_uuid, project_uuid,
                  session_uuid, message_uuid, import_run_uuid, job_uuid,
                  latency_ms, status
                ) VALUES (?,?,?,?,?,?,1,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(usage_uuid) DO NOTHING
                """,
                (
                    _semantic_usage_uuid(stage_execution_uuid),
                    model_profile_uuid,
                    ModelRole.MEMORY_EXTRACTION.value,
                    outcome.input_tokens,
                    outcome.output_tokens,
                    now,
                    _SEMANTIC_STAGE,
                    provider_type,
                    model_name,
                    scope.workspace_uuid,
                    scope.project_uuid,
                    scope.session_uuid,
                    message_uuid,
                    import_run_uuid,
                    job_uuid,
                    outcome.latency_ms,
                    outcome.status,
                ),
            )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

    def assert_runtime_snapshot(
        self,
        *,
        message_uuid: str,
        processing_run_uuid: str,
        raw_text_hash: str,
    ) -> None:
        row = self.connection.execute(
            """
            SELECT message.raw_text, run.message_uuid AS run_message_uuid
            FROM messages message
            JOIN memory_processing_runs run
              ON run.processing_run_uuid = ?
            WHERE message.message_uuid = ?
            """,
            (processing_run_uuid, message_uuid),
        ).fetchone()
        if (
            row is None
            or row["run_message_uuid"] != message_uuid
            or hashlib.sha256(str(row["raw_text"] or "").encode("utf-8")).hexdigest()
            != raw_text_hash
        ):
            raise RuntimeError("semantic runtime source snapshot changed")

    def persist_coverage_plan(
        self,
        plan: CoveragePlan,
        bindings: CoveragePersistenceBindings,
    ) -> dict[str, Any]:
        return self.coverage.persist_plan(plan, bindings)

    def ensure_semantic_span_authorities(
        self,
        *,
        scope: CurrentContextScope,
        processing_run_uuid: str,
        semantic_output: SemanticAnalysisV1Output,
        authorities: Sequence[PersistedUnitAuthority],
    ) -> Sequence[PersistedUnitAuthority]:
        _insert_missing_semantic_text_units(
            self.connection,
            postgres=False,
            scope=scope,
            semantic_output=semantic_output,
            authorities=authorities,
        )
        # SQLite coverage commands deliberately start their own IMMEDIATE
        # transaction and roll back any open transaction first.  Make the
        # synthetic source authority durable before handing control to that
        # legacy command boundary.
        self.connection.commit()
        return self.load_persisted_authorities(
            message_uuid=scope.message_uuid,
            processing_run_uuid=processing_run_uuid,
        )

    def reserve_and_link_candidate(
        self,
        *,
        proposal: CandidateProposal,
        coverage_item_id: str,
        candidate: MemoryCandidate,
        evidence: CandidateEvidence,
        payload_hash: str,
        authority: CandidateAuthorityBinding | None,
    ) -> dict[str, Any]:
        self.coverage.reserve_candidate(
            proposal.proposal_id,
            coverage_item_id,
            payload_hash,
        )
        return self.coverage.create_and_link_candidate(
            proposal,
            candidate,
            (evidence,),
            authority,
        )


class PostgresSemanticCandidateRuntimeAdapter:
    postgres = True

    def __init__(self, connection: Any) -> None:
        self.connection = connection
        self.coverage = PostgresSemanticCoverageRepository(connection)

    def load_current_context_scope(self, message_uuid: str) -> CurrentContextScope:
        row = self.connection.execute(
            """
            WITH latest_version AS (
              SELECT message_version_uuid, message_uuid, raw_text,
                     ROW_NUMBER() OVER (
                       PARTITION BY message_uuid
                       ORDER BY version_number DESC, message_version_uuid DESC
                     ) AS position
              FROM message_versions
            )
            SELECT m.message_uuid, m.session_uuid, m.role, m.turn_index, m.raw_text,
                   m.visibility, m.is_deleted,
                   COALESCE(m.redaction_status, 'none') AS redaction_status,
                   s.workspace_uuid, s.project_uuid,
                   actor.user_uuid, actor.workspace_uuid AS actor_workspace_uuid,
                   version.message_version_uuid,
                   version.raw_text AS version_raw_text
            FROM messages m
            JOIN sessions s ON s.session_uuid = m.session_uuid
            LEFT JOIN memorist_session_actors actor
              ON actor.session_uuid = m.session_uuid
            LEFT JOIN latest_version version
              ON version.message_uuid = m.message_uuid
             AND version.position = 1
            WHERE m.message_uuid = %s
            """,
            (message_uuid,),
        ).fetchone()
        if row is None:
            raise ValueError(f"message not found: {message_uuid}")
        raw_text = str(row.get("raw_text") or "")
        version_uuid = (
            str(row["message_version_uuid"])
            if row.get("message_version_uuid") is not None
            and (row.get("version_raw_text") is None or str(row["version_raw_text"]) == raw_text)
            else None
        )
        return CurrentContextScope(
            message_uuid=str(row["message_uuid"]),
            message_version_uuid=version_uuid,
            session_uuid=str(row["session_uuid"]),
            workspace_uuid=_optional_string(row.get("workspace_uuid")),
            project_uuid=_optional_string(row.get("project_uuid")),
            user_uuid=_optional_string(row.get("user_uuid")),
            actor_workspace_uuid=_optional_string(row.get("actor_workspace_uuid")),
            role=str(row["role"]),
            turn_index=int(row["turn_index"]) if row.get("turn_index") is not None else None,
            raw_text=raw_text,
            visibility=str(row["visibility"]),
            is_deleted=bool(row["is_deleted"]),
            redaction_status=str(row["redaction_status"]),
        )

    def list_prior_context_records(
        self,
        scope: CurrentContextScope,
        *,
        scan_limit: int,
    ) -> Sequence[PriorContextRecord]:
        if scope.turn_index is None:
            return ()
        rows = self.connection.execute(
            """
            WITH latest_version AS (
              SELECT message_version_uuid, message_uuid, raw_text,
                     ROW_NUMBER() OVER (
                       PARTITION BY message_uuid
                       ORDER BY version_number DESC, message_version_uuid DESC
                     ) AS position
              FROM message_versions
            )
            SELECT actor.user_uuid, m.session_uuid, s.workspace_uuid, s.project_uuid,
                   m.message_uuid, version.message_version_uuid,
                   version.raw_text AS version_raw_text, m.role, m.turn_index,
                   m.visibility, m.is_deleted,
                   COALESCE(m.redaction_status, 'none') AS redaction_status,
                   unit.text_unit_uuid, unit.unit_index,
                   unit.start_char AS raw_start, unit.end_char AS raw_end,
                   unit.text AS unit_text
            FROM messages m
            JOIN sessions s ON s.session_uuid = m.session_uuid
            LEFT JOIN memorist_session_actors actor
              ON actor.session_uuid = m.session_uuid
            JOIN text_units unit ON unit.message_uuid = m.message_uuid
            LEFT JOIN latest_version version
              ON version.message_uuid = m.message_uuid
             AND version.position = 1
            WHERE m.session_uuid = %s
              AND m.message_uuid <> %s
              AND m.turn_index < %s
            ORDER BY m.turn_index DESC, unit.unit_index DESC,
                     m.message_uuid DESC, unit.text_unit_uuid DESC
            LIMIT %s
            """,
            (
                scope.session_uuid,
                scope.message_uuid,
                scope.turn_index,
                scan_limit,
            ),
        ).fetchall()
        return tuple(_prior_context_record(row) for row in rows)

    def load_persisted_authorities(
        self,
        *,
        message_uuid: str,
        processing_run_uuid: str,
    ) -> Sequence[PersistedUnitAuthority]:
        rows = self.connection.execute(
            """
            WITH selected_analysis AS (
              SELECT analysis_run_uuid
              FROM jakobson_analysis_runs
              WHERE message_uuid = %s
              ORDER BY created_at DESC, analysis_run_uuid DESC
              LIMIT 1
            )
            SELECT unit.text_unit_uuid, unit.start_char, unit.end_char, unit.text,
                   gate.gate_decision_uuid, gate.decision,
                   annotation.annotation_uuid,
                   route.route_uuid, route.route_type, route.status, route.priority
            FROM text_units unit
            LEFT JOIN memory_gate_decisions gate
              ON gate.text_unit_uuid = unit.text_unit_uuid
             AND gate.processing_run_uuid = %s
            LEFT JOIN jakobson_sentence_annotations annotation
              ON annotation.unit_uuid = unit.text_unit_uuid
             AND annotation.analysis_run_uuid = (
               SELECT analysis_run_uuid FROM selected_analysis
             )
            LEFT JOIN memory_signal_routes route
              ON route.annotation_uuid = annotation.annotation_uuid
            WHERE unit.message_uuid = %s
            ORDER BY unit.unit_index, route.priority DESC, route.route_uuid
            """,
            (message_uuid, processing_run_uuid, message_uuid),
        ).fetchall()
        return _authorities_from_rows(rows)

    def load_completed_semantic_planning(
        self,
        *,
        message_uuid: str,
        processing_run_uuid: str,
        message_version_uuid: str | None,
        raw_text_hash: str,
        semantic_contract_hash: str,
        route_mapping_version: str,
        provenance_policy_version: str,
        privacy_policy_version: str,
        current_authorities: Sequence[PersistedUnitAuthority],
    ) -> RecordedSemanticPlanningReplay | None:
        row = self.connection.execute(
            """
            SELECT coverage.coverage_run_uuid, coverage.plan_jsonb,
                   coverage.processing_run_uuid
            FROM semantic_coverage_runs coverage
            WHERE coverage.message_uuid = %s
              AND coverage.raw_text_hash = %s
              AND coverage.semantic_contract_hash = %s
              AND coverage.route_mapping_version = %s
              AND coverage.provenance_policy_version = %s
              AND coverage.privacy_policy_version = %s
              AND coverage.message_version_uuid IS NOT DISTINCT FROM %s
              AND (CAST(%s AS TEXT) IS NOT NULL OR coverage.processing_run_uuid = %s)
            ORDER BY CASE WHEN coverage.processing_run_uuid = %s THEN 0 ELSE 1 END,
                     coverage.created_at DESC, coverage.coverage_run_uuid DESC
            LIMIT 1
            """,
            (
                message_uuid,
                raw_text_hash,
                semantic_contract_hash,
                route_mapping_version,
                provenance_policy_version,
                privacy_policy_version,
                message_version_uuid,
                message_version_uuid,
                processing_run_uuid,
                processing_run_uuid,
            ),
        ).fetchone()
        if row is None:
            same_identity = self.connection.execute(
                """
                SELECT message_version_uuid, route_mapping_version,
                       provenance_policy_version, privacy_policy_version
                FROM semantic_coverage_runs
                WHERE message_uuid = %s
                  AND raw_text_hash = %s
                  AND semantic_contract_hash = %s
                LIMIT 1
                """,
                (
                    message_uuid,
                    raw_text_hash,
                    semantic_contract_hash,
                ),
            ).fetchone()
            if same_identity is not None:
                if _optional_string(
                    same_identity["message_version_uuid"]
                ) == message_version_uuid and (
                    same_identity["route_mapping_version"] != route_mapping_version
                    or same_identity["provenance_policy_version"] != provenance_policy_version
                    or same_identity["privacy_policy_version"] != privacy_policy_version
                ):
                    raise RuntimeError(
                        "semantic replay policy version changed for frozen proposal identity"
                    )
                raise RuntimeError(
                    "same-text message version conflicts with frozen proposal identity"
                )
            return None
        raw_plan = row["plan_jsonb"]
        if isinstance(raw_plan, str):
            plan = CoveragePlan.model_validate_json(raw_plan)
        else:
            plan = CoveragePlan.model_validate_json(json.dumps(raw_plan))
        links = self.connection.execute(
            """
            SELECT item.coverage_item_uuid, item.raw_start, item.raw_end, item.disposition,
                   item.proposal_uuid, link.candidate_uuid, link.state,
                   gate.decision AS gate_decision,
                   route.route_type, route.status AS route_status,
                   candidate.sensitivity AS sensitivity_class
            FROM semantic_coverage_items item
            LEFT JOIN semantic_candidate_links link
              ON link.coverage_item_uuid = item.coverage_item_uuid
            LEFT JOIN memory_candidates candidate
              ON candidate.candidate_uuid = link.candidate_uuid
            LEFT JOIN memory_gate_decisions gate
              ON gate.gate_decision_uuid = item.gate_decision_uuid
            LEFT JOIN memory_signal_routes route
              ON route.route_uuid = item.route_uuid
            WHERE item.coverage_run_uuid = %s
            ORDER BY item.raw_start, item.raw_end, item.coverage_item_uuid
            """,
            (str(row["coverage_run_uuid"]),),
        ).fetchall()
        complete = _validate_completed_replay(plan, links, current_authorities)
        if complete is None:
            if str(row["processing_run_uuid"]) != processing_run_uuid:
                raise RuntimeError("canonical cross-run semantic replay is incomplete")
            return None
        return RecordedSemanticPlanningReplay(
            plan=plan,
            candidate_uuids=complete,
            semantic_stage_execution_uuid=None,
        )

    def load_semantic_execution(
        self,
        *,
        stage_execution_uuid: str,
        input_hash: str,
        contract_hash: str,
    ) -> RecordedSemanticExecution | None:
        prompt_uuid = _semantic_prompt_uuid(stage_execution_uuid)
        row = self.connection.execute(
            """
            SELECT prompt.validated_output_ijson, prompt.input_hash,
                   stage.contract_hash, stage.stage_execution_uuid
            FROM prompt_execution_runs prompt
            JOIN processing_stage_runs stage
              ON stage.stage_execution_uuid = %s
            WHERE prompt.prompt_execution_uuid = %s
              AND prompt.prompt_id = %s
              AND prompt.prompt_version = %s
              AND stage.stage = %s
            """,
            (
                stage_execution_uuid,
                prompt_uuid,
                SEMANTIC_CANDIDATE_ANALYSIS_PROMPT_ID,
                SEMANTIC_CANDIDATE_ANALYSIS_VERSION,
                _SEMANTIC_STAGE,
            ),
        ).fetchone()
        return _recorded_execution(row, prompt_uuid, input_hash, contract_hash)

    def record_semantic_execution(
        self,
        *,
        prompt_execution_uuid: str,
        stage_execution_uuid: str,
        processing_run_uuid: str,
        input_payload: Mapping[str, Any],
        outcome: ContractExecutionOutcome,
        profile: Mapping[str, Any],
        message_uuid: str,
        import_run_uuid: str | None,
        job_uuid: str | None,
        contract_hash: str,
        profile_fingerprint: str,
    ) -> None:
        scope = self.load_current_context_scope(message_uuid)
        input_hash = canonical_hash_ijson(input_payload)
        output_hash = canonical_hash_ijson(outcome.output)
        existing = self.load_semantic_execution(
            stage_execution_uuid=stage_execution_uuid,
            input_hash=input_hash,
            contract_hash=contract_hash,
        )
        if existing is not None:
            return
        now = utc_now()
        provider_type = str(
            profile.get("provider_type") or profile.get("provider") or "deterministic"
        )
        model_name = str(profile.get("model_name") or provider_type)
        model_profile_uuid = _optional_string(profile.get("model_profile_uuid"))
        requested_role = str(
            profile.get("requested_role")
            or profile.get("model_role")
            or ModelRole.MEMORY_EXTRACTION.value
        )
        effective_role = str(
            profile.get("effective_role")
            or profile.get("model_role")
            or ModelRole.MEMORY_EXTRACTION.value
        )
        warnings = _safe_codes(outcome.output.get("warnings"))
        with _postgres_transaction(self.connection):
            self.connection.execute(
                """
                INSERT INTO prompt_execution_runs (
                  prompt_execution_uuid, prompt_id, prompt_version, stage,
                  model_profile_uuid, model_role, provider_type, model_name,
                  workspace_uuid, project_uuid, session_uuid, message_uuid,
                  import_run_uuid, job_uuid, input_hash, output_hash, input_ref,
                  raw_output_ijson, validated_output_ijson, status,
                  warnings_ijson, error_sanitized, latency_ms, input_tokens,
                  output_tokens, created_at, schema_version
                ) VALUES (
                  %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                  %s,%s,%s,%s,%s,%s,%s,%s,%s,1
                )
                """,
                (
                    prompt_execution_uuid,
                    SEMANTIC_CANDIDATE_ANALYSIS_PROMPT_ID,
                    SEMANTIC_CANDIDATE_ANALYSIS_VERSION,
                    _SEMANTIC_STAGE,
                    model_profile_uuid,
                    ModelRole.MEMORY_EXTRACTION.value,
                    provider_type,
                    model_name,
                    scope.workspace_uuid,
                    scope.project_uuid,
                    scope.session_uuid,
                    message_uuid,
                    import_run_uuid,
                    job_uuid,
                    input_hash,
                    output_hash,
                    f"message:{message_uuid}:text-envelope:{input_payload['text_envelope']['raw_text_hash']}",
                    dump_ijson(outcome.output),
                    dump_ijson(outcome.output),
                    outcome.status,
                    dump_ijson(warnings),
                    None,
                    outcome.latency_ms,
                    outcome.input_tokens,
                    outcome.output_tokens,
                    now,
                ),
            )
            stage = _stage_values(
                stage_execution_uuid=stage_execution_uuid,
                processing_run_uuid=processing_run_uuid,
                job_uuid=job_uuid,
                message_uuid=message_uuid,
                requested_role=requested_role,
                effective_role=effective_role,
                model_profile_uuid=model_profile_uuid,
                provider_type=provider_type,
                model_name=model_name,
                scope_source=str(profile.get("scope_source") or "runtime_profile"),
                inheritance_source=_optional_string(profile.get("inheritance_source")),
                contract_hash=contract_hash,
                profile_fingerprint=profile_fingerprint,
                input_hash=input_hash,
                output_hash=output_hash,
                outcome=outcome,
                now=now,
                postgres=True,
            )
            columns = list(stage)
            placeholders = [
                "%s::jsonb" if column == "validation_errors_jsonb" else "%s" for column in columns
            ]
            self.connection.execute(
                f"INSERT INTO processing_stage_runs ({', '.join(columns)}) "
                f"VALUES ({', '.join(placeholders)})",
                tuple(stage[column] for column in columns),
            )
            persist_message_semantics(
                self.connection,
                postgres=True,
                message_uuid=message_uuid,
                processing_run_uuid=processing_run_uuid,
                prompt_execution_uuid=prompt_execution_uuid,
                stage_execution_uuid=stage_execution_uuid,
                contract_hash=contract_hash,
                scope=scope,
                input_payload=input_payload,
                outcome=outcome,
            )
            self.connection.execute(
                """
                INSERT INTO model_usage_events (
                  usage_event_uuid, model_profile_uuid, role, event_type,
                  input_tokens, output_tokens, created_at, schema_version,
                  stage, provider_type, model_name, workspace_uuid, project_uuid,
                  session_uuid, message_uuid, import_run_uuid, job_uuid,
                  latency_ms, status
                ) VALUES (
                  %s,%s,%s,%s,%s,%s,%s,1,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
                )
                ON CONFLICT (usage_event_uuid) DO NOTHING
                """,
                (
                    _semantic_usage_uuid(stage_execution_uuid),
                    model_profile_uuid,
                    ModelRole.MEMORY_EXTRACTION.value,
                    "prompt_execution",
                    outcome.input_tokens,
                    outcome.output_tokens,
                    now,
                    _SEMANTIC_STAGE,
                    provider_type,
                    model_name,
                    scope.workspace_uuid,
                    scope.project_uuid,
                    scope.session_uuid,
                    message_uuid,
                    import_run_uuid,
                    job_uuid,
                    outcome.latency_ms,
                    outcome.status,
                ),
            )

    def assert_runtime_snapshot(
        self,
        *,
        message_uuid: str,
        processing_run_uuid: str,
        raw_text_hash: str,
    ) -> None:
        row = self.connection.execute(
            """
            SELECT message.raw_text, run.message_uuid AS run_message_uuid
            FROM messages message
            JOIN memory_processing_runs run
              ON run.processing_run_uuid = %s
            WHERE message.message_uuid = %s
            """,
            (processing_run_uuid, message_uuid),
        ).fetchone()
        if (
            row is None
            or row["run_message_uuid"] != message_uuid
            or hashlib.sha256(str(row.get("raw_text") or "").encode("utf-8")).hexdigest()
            != raw_text_hash
        ):
            raise RuntimeError("semantic runtime source snapshot changed")

    def persist_coverage_plan(
        self,
        plan: CoveragePlan,
        bindings: CoveragePersistenceBindings,
    ) -> dict[str, Any]:
        return self.coverage.persist_plan(plan, bindings)

    def ensure_semantic_span_authorities(
        self,
        *,
        scope: CurrentContextScope,
        processing_run_uuid: str,
        semantic_output: SemanticAnalysisV1Output,
        authorities: Sequence[PersistedUnitAuthority],
    ) -> Sequence[PersistedUnitAuthority]:
        _insert_missing_semantic_text_units(
            self.connection,
            postgres=True,
            scope=scope,
            semantic_output=semantic_output,
            authorities=authorities,
        )
        return self.load_persisted_authorities(
            message_uuid=scope.message_uuid,
            processing_run_uuid=processing_run_uuid,
        )

    def reserve_and_link_candidate(
        self,
        *,
        proposal: CandidateProposal,
        coverage_item_id: str,
        candidate: MemoryCandidate,
        evidence: CandidateEvidence,
        payload_hash: str,
        authority: CandidateAuthorityBinding | None,
    ) -> dict[str, Any]:
        self.coverage.reserve_candidate(
            proposal.proposal_id,
            coverage_item_id,
            payload_hash,
        )
        return self.coverage.create_and_link_candidate(
            proposal,
            candidate,
            (evidence,),
            authority,
        )


def _insert_missing_semantic_text_units(
    connection: Any,
    *,
    postgres: bool,
    scope: CurrentContextScope,
    semantic_output: SemanticAnalysisV1Output,
    authorities: Sequence[PersistedUnitAuthority],
) -> None:
    missing = [
        unit
        for unit in semantic_output.semantic_units
        if not any(
            authority.raw_start <= unit.raw_start and unit.raw_end <= authority.raw_end
            for authority in authorities
        )
    ]
    if not missing:
        return
    placeholder = "%s" if postgres else "?"
    row = connection.execute(
        f"SELECT COALESCE(MAX(unit_index), -1) AS max_index FROM text_units "
        f"WHERE message_uuid = {placeholder}",
        (scope.message_uuid,),
    ).fetchone()
    start_index = int(row["max_index"] if row is not None else -1) + 1
    for offset, unit in enumerate(missing):
        text = scope.raw_text[unit.raw_start : unit.raw_end]
        identity = (
            f"{scope.message_uuid}:{hashlib.sha256(scope.raw_text.encode('utf-8')).hexdigest()}:"
            f"{unit.raw_start}:{unit.raw_end}"
        )
        text_unit_uuid = str(uuid.uuid5(_SEMANTIC_TEXT_UNIT_NAMESPACE, identity))
        unit_index = start_index + offset
        content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if postgres:
            connection.execute(
                """
                INSERT INTO text_units (
                  text_unit_uuid, unit_uuid, message_uuid, session_uuid, speaker_role,
                  unit_type, unit_index, text, start_char, end_char, char_start,
                  char_end, segmentation_confidence, segmentation_notes, content_hash,
                  created_at, schema_version
                ) VALUES (%s,%s,%s,%s,%s,'fragment',%s,%s,%s,%s,%s,%s,'high',
                          'semantic_multi_unit_span',%s,%s,1)
                ON CONFLICT (message_uuid, unit_type, unit_index) DO NOTHING
                """,
                (
                    text_unit_uuid,
                    text_unit_uuid,
                    scope.message_uuid,
                    scope.session_uuid,
                    scope.role,
                    unit_index,
                    text,
                    unit.raw_start,
                    unit.raw_end,
                    unit.raw_start,
                    unit.raw_end,
                    content_hash,
                    utc_now(),
                ),
            )
        else:
            connection.execute(
                """
                INSERT OR IGNORE INTO text_units (
                  text_unit_uuid, message_uuid, session_uuid, unit_index, unit_type,
                  text, start_char, end_char, speaker_role, content_hash, created_at,
                  schema_version
                ) VALUES (?, ?, ?, ?, 'fragment', ?, ?, ?, ?, ?, ?, 1)
                """,
                (
                    text_unit_uuid,
                    scope.message_uuid,
                    scope.session_uuid,
                    unit_index,
                    text,
                    unit.raw_start,
                    unit.raw_end,
                    scope.role,
                    content_hash,
                    utc_now(),
                ),
            )


def _prior_context_record(row: Mapping[str, Any]) -> PriorContextRecord:
    return PriorContextRecord(
        user_uuid=_optional_string(row["user_uuid"]),
        session_uuid=str(row["session_uuid"]),
        workspace_uuid=_optional_string(row["workspace_uuid"]),
        project_uuid=_optional_string(row["project_uuid"]),
        message_uuid=str(row["message_uuid"]),
        message_version_uuid=_optional_string(row["message_version_uuid"]),
        version_raw_text=(
            str(row["version_raw_text"]) if row["version_raw_text"] is not None else None
        ),
        role=str(row["role"]),
        turn_index=int(row["turn_index"]) if row["turn_index"] is not None else None,
        visibility=str(row["visibility"]),
        is_deleted=bool(row["is_deleted"]),
        redaction_status=str(row["redaction_status"]),
        text_unit_uuid=str(row["text_unit_uuid"]),
        unit_index=int(row["unit_index"]),
        raw_start=int(row["raw_start"]),
        raw_end=int(row["raw_end"]),
        unit_text=str(row["unit_text"]),
    )


def _authorities_from_rows(
    rows: Sequence[Mapping[str, Any]],
) -> tuple[PersistedUnitAuthority, ...]:
    by_unit: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        by_unit[str(row["text_unit_uuid"])].append(row)
    authorities: list[PersistedUnitAuthority] = []
    for unit_uuid, unit_rows in by_unit.items():
        first = unit_rows[0]
        routes = [row for row in unit_rows if row["route_uuid"] is not None]
        selected = min(routes, key=_route_order) if routes else None
        sensitivity = classify_sensitivity(str(first["text"]))
        gate_ids = {
            str(row["gate_decision_uuid"])
            for row in unit_rows
            if row["gate_decision_uuid"] is not None
        }
        annotation_ids = {
            str(row["annotation_uuid"]) for row in unit_rows if row["annotation_uuid"] is not None
        }
        authorities.append(
            PersistedUnitAuthority(
                text_unit_uuid=unit_uuid,
                raw_start=int(first["start_char"]),
                raw_end=int(first["end_char"]),
                annotation_uuid=(
                    str(selected["annotation_uuid"])
                    if selected is not None and selected["annotation_uuid"] is not None
                    else None
                ),
                gate_decision_uuid=(
                    str(first["gate_decision_uuid"])
                    if first["gate_decision_uuid"] is not None
                    else None
                ),
                gate_decision=(str(first["decision"]) if first["decision"] is not None else None),
                route_uuid=(str(selected["route_uuid"]) if selected is not None else None),
                route_type=(str(selected["route_type"]) if selected is not None else None),
                route_status=(str(selected["status"]) if selected is not None else None),
                privacy_ceiling=sensitivity.value,
                privacy_storage_allowed=sensitivity is not SensitivityClass.SECRET,
                conflicting_authority=len(gate_ids) > 1 or len(annotation_ids) > 1,
            )
        )
    authorities.sort(key=lambda item: (item.raw_start, item.raw_end, item.text_unit_uuid))
    return tuple(authorities)


def _validate_completed_replay(
    plan: CoveragePlan,
    rows: Sequence[Mapping[str, Any]],
    current_authorities: Sequence[PersistedUnitAuthority],
) -> tuple[str, ...] | None:
    """Validate immutable plan content against current-run server authority."""

    by_id = {str(row["coverage_item_uuid"]): row for row in rows}
    if set(by_id) != {item.coverage_item_id for item in plan.items}:
        raise RuntimeError("persisted semantic replay item set changed")
    candidates: list[str] = []
    for item in plan.items:
        row = by_id[item.coverage_item_id]
        if (
            int(row["raw_start"]) != item.raw_start
            or int(row["raw_end"]) != item.raw_end
            or str(row["disposition"]) != item.disposition.value
            or _optional_string(row["proposal_uuid"]) != item.proposal_id
        ):
            raise RuntimeError("persisted semantic replay item content changed")
        containing = [
            authority
            for authority in current_authorities
            if authority.raw_start <= item.raw_start and item.raw_end <= authority.raw_end
        ]
        current = containing[0] if len(containing) == 1 else None
        if current is None and item.proposal_id is not None:
            raise RuntimeError("current-run semantic replay authority is incomplete")
        # Legacy gate and route values remain immutable audit fields on the
        # persisted plan. Changes to those annotations do not invalidate the
        # model-led semantic replay; only source/privacy authority can do so.
        if item.proposal_id is None:
            continue
        if row["state"] != "candidate_linked" or row["candidate_uuid"] != row["proposal_uuid"]:
            return None
        if (
            current is None
            or not current.privacy_storage_allowed
            or current.privacy_ceiling != str(row["sensitivity_class"])
        ):
            raise RuntimeError("semantic replay authority changed: durable candidate")
        candidates.append(str(row["candidate_uuid"]))
    return tuple(candidates)


def _route_order(row: Mapping[str, Any]) -> tuple[bool, bool, int, str]:
    return (
        str(row["status"]) != "ready",
        str(row["route_type"]) == "ignore",
        -int(row["priority"] or 0),
        str(row["route_uuid"]),
    )


def _recorded_execution(
    row: Mapping[str, Any] | None,
    prompt_uuid: str,
    input_hash: str,
    contract_hash: str,
) -> RecordedSemanticExecution | None:
    if row is None:
        return None
    if row["input_hash"] != input_hash or row["contract_hash"] != contract_hash:
        raise RuntimeError("semantic execution replay identity conflict")
    value = row["validated_output_ijson"]
    output = load_ijson(str(value)) if isinstance(value, str) else value
    if not isinstance(output, dict):
        raise RuntimeError("semantic execution replay output is missing")
    return RecordedSemanticExecution(
        prompt_execution_uuid=prompt_uuid,
        stage_execution_uuid=str(row["stage_execution_uuid"]),
        output=dict(output),
    )


def _stage_values(
    *,
    stage_execution_uuid: str,
    processing_run_uuid: str,
    job_uuid: str | None,
    message_uuid: str,
    requested_role: str,
    effective_role: str,
    model_profile_uuid: str | None,
    provider_type: str,
    model_name: str,
    scope_source: str,
    inheritance_source: str | None,
    contract_hash: str,
    profile_fingerprint: str,
    input_hash: str,
    output_hash: str,
    outcome: ContractExecutionOutcome,
    now: str,
    postgres: bool = False,
) -> dict[str, Any]:
    validation_column = "validation_errors_jsonb" if postgres else "validation_errors_ijson"
    return {
        "stage_execution_uuid": stage_execution_uuid,
        "processing_run_uuid": processing_run_uuid,
        "job_uuid": job_uuid,
        "source_type": "message",
        "source_uuid": message_uuid,
        "requested_role": requested_role,
        "effective_role": effective_role,
        "stage": _SEMANTIC_STAGE,
        "model_profile_uuid": model_profile_uuid,
        "provider_type": provider_type,
        "model_name": model_name,
        "prompt_id": SEMANTIC_CANDIDATE_ANALYSIS_PROMPT_ID,
        "prompt_version": SEMANTIC_CANDIDATE_ANALYSIS_VERSION,
        "contract_hash": contract_hash,
        "profile_fingerprint": profile_fingerprint,
        "input_hash": input_hash,
        "output_hash": output_hash,
        "status": outcome.status,
        "called_provider": outcome.called_provider,
        "fallback_used": outcome.fallback_used,
        "scope_source": scope_source,
        "inheritance_source": inheritance_source,
        "fallback_reason": outcome.fallback_reason,
        "detail_sanitized": None,
        validation_column: (
            json.dumps(outcome.validation_error_paths)
            if postgres
            else dump_ijson(outcome.validation_error_paths)
        ),
        "input_tokens": outcome.input_tokens,
        "output_tokens": outcome.output_tokens,
        "embedding_count": 0,
        "latency_ms": outcome.latency_ms,
        "idempotency_key": (f"semantic:{processing_run_uuid}:{message_uuid}:{contract_hash}"),
        "created_at": now,
        "completed_at": now,
        "schema_version": 1,
        "provider_output_valid": outcome.provider_output_valid,
        "repair_attempted": outcome.repair_attempted,
        "repair_succeeded": outcome.repair_succeeded,
        "parse_status": outcome.parse_status,
        "capability_mode": outcome.capability_mode,
        "provider_response_id": outcome.provider_response_id,
        "canonicalized": outcome.canonicalized,
        "attempt_count": outcome.attempt_count,
        "total_provider_latency_ms": outcome.latency_ms,
    }


def _safe_codes(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value[:20]:
        safe = "".join(
            character
            for character in str(item).lower().replace(" ", "_")
            if character.isalnum() or character in {"_", "-"}
        )[:80]
        if safe:
            result.append(safe)
    return result


def _semantic_prompt_uuid(stage_execution_uuid: str) -> str:
    return stable_stage_execution_uuid(f"semantic-prompt:{stage_execution_uuid}")


def _semantic_usage_uuid(stage_execution_uuid: str) -> str:
    return stable_stage_execution_uuid(f"semantic-usage:{stage_execution_uuid}")


def _optional_string(value: Any) -> str | None:
    return str(value) if value not in {None, ""} else None


@contextmanager
def _postgres_transaction(connection: Any) -> Any:
    raw = getattr(connection, "raw", connection)
    connection.commit()
    with raw.transaction():
        yield
