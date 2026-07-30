"""Transactional PostgreSQL persistence for WP02 coverage and replay links."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from typing import Any

from memcore.memory_worker.semantic.coverage import CandidateProposal, CoveragePlan
from memcore.memory_worker.semantic_coverage_persistence import (
    CandidateAuthorityBinding,
    CoveragePersistenceBindings,
    SemanticCoverageIdentityConflict,
    candidate_payload_hash,
    coverage_run_uuid,
    validate_candidate_binding,
)
from memcore.models import CandidateEvidence, CandidateStatus, MemoryCandidate, utc_now


class PostgresSemanticCoverageRepository:
    def __init__(self, connection: Any) -> None:
        self.connection = connection

    def persist_plan(
        self, plan: CoveragePlan, bindings: CoveragePersistenceBindings
    ) -> dict[str, Any]:
        run_uuid = coverage_run_uuid(plan.coverage_hash)
        with _transaction(self.connection):
            inserted = self.connection.execute(
                """
                INSERT INTO semantic_coverage_runs (
                  coverage_run_uuid, coverage_plan_version, coverage_hash,
                  message_uuid, message_version_uuid, processing_run_uuid,
                  semantic_prompt_execution_uuid, raw_text_hash,
                  text_envelope_contract_version, semantic_contract_hash,
                  route_mapping_version, provenance_policy_version,
                  privacy_policy_version,
                  status, plan_jsonb, warnings_jsonb, created_at, schema_version
                )
                VALUES (
                  %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,1
                )
                ON CONFLICT (coverage_run_uuid) DO NOTHING
                """,
                (
                    run_uuid,
                    plan.coverage_plan_version,
                    plan.coverage_hash,
                    plan.message_uuid,
                    bindings.message_version_uuid,
                    plan.processing_run_uuid,
                    plan.semantic_prompt_execution_uuid,
                    plan.raw_text_hash,
                    bindings.text_envelope_contract_version,
                    plan.semantic_contract_hash,
                    bindings.route_mapping_version,
                    bindings.provenance_policy_version,
                    bindings.privacy_policy_version,
                    plan.status,
                    json.dumps(plan.model_dump(mode="json"), sort_keys=True),
                    json.dumps(list(plan.warnings)),
                    utc_now(),
                ),
            ).rowcount
            if inserted:
                for item in plan.items:
                    fingerprint = (
                        bindings.semantic_unit_fingerprints.get(item.semantic_unit_id)
                        if item.semantic_unit_id is not None
                        else None
                    )
                    if item.semantic_unit_id is not None and fingerprint is None:
                        raise ValueError(
                            "every semantic coverage item requires its canonical fingerprint"
                        )
                    self.connection.execute(
                        """
                        INSERT INTO semantic_coverage_items (
                          coverage_item_uuid, coverage_run_uuid, semantic_unit_id,
                          semantic_unit_fingerprint, raw_start, raw_end, disposition,
                          gate_decision_uuid, route_uuid, annotation_uuid, proposal_uuid,
                          reason_codes_jsonb, created_at, schema_version
                        )
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,1)
                        """,
                        (
                            item.coverage_item_id,
                            run_uuid,
                            item.semantic_unit_id,
                            fingerprint,
                            item.raw_start,
                            item.raw_end,
                            item.disposition.value,
                            item.gate_decision_uuid,
                            item.route_uuid,
                            bindings.annotation_uuids.get(item.coverage_item_id),
                            item.proposal_id,
                            json.dumps(list(item.reason_codes)),
                            utc_now(),
                        ),
                    )
            else:
                self._assert_plan_replay(plan, bindings)
        return {
            "target_uuid": run_uuid,
            "state": "created" if inserted else "existing",
        }

    def reserve_candidate(
        self, proposal_id: str, coverage_item_id: str, payload_hash: str
    ) -> dict[str, Any]:
        with _transaction(self.connection):
            self.connection.execute(
                """
                INSERT INTO semantic_candidate_links (
                  proposal_uuid, coverage_item_uuid, candidate_uuid, payload_hash,
                  state, attempted_at, linked_at, updated_at, schema_version
                )
                SELECT %s, coverage_item_uuid, NULL, %s,
                       'candidate_creation_attempted', now(), NULL, now(), 1
                FROM semantic_coverage_items
                WHERE coverage_item_uuid = %s
                  AND disposition = 'durable_candidate'
                  AND proposal_uuid = %s
                ON CONFLICT (proposal_uuid) DO NOTHING
                """,
                (proposal_id, payload_hash, coverage_item_id, proposal_id),
            )
            row = _fetch_one(
                self.connection,
                """
                SELECT proposal_uuid, coverage_item_uuid, candidate_uuid,
                       payload_hash, state
                FROM semantic_candidate_links
                WHERE proposal_uuid = %s
                FOR UPDATE
                """,
                (proposal_id,),
            )
            if row is None:
                raise SemanticCoverageIdentityConflict(
                    "reservation does not match durable coverage"
                )
            _assert_link_row(row, coverage_item_id, payload_hash)
        return {
            "target_uuid": proposal_id,
            "state": "existing" if row["state"] == "candidate_linked" else "reserved",
        }

    def create_and_link_candidate(
        self,
        proposal: CandidateProposal,
        candidate: MemoryCandidate,
        evidence_items: Sequence[CandidateEvidence],
        authority: CandidateAuthorityBinding | None = None,
    ) -> dict[str, Any]:
        evidence = validate_candidate_binding(proposal, candidate, evidence_items)
        payload_hash = candidate_payload_hash(candidate, evidence)
        with _transaction(self.connection):
            link = _fetch_one(
                self.connection,
                """
                SELECT proposal_uuid, coverage_item_uuid, candidate_uuid,
                       payload_hash, state
                FROM semantic_candidate_links
                WHERE proposal_uuid = %s
                FOR UPDATE
                """,
                (proposal.proposal_id,),
            )
            if link is None:
                raise SemanticCoverageIdentityConflict("candidate must be reserved before creation")
            _assert_link_row(link, str(link["coverage_item_uuid"]), payload_hash)
            if authority is not None:
                self._assert_candidate_authority(authority)
            if link["state"] == "candidate_linked":
                existing_candidate = _fetch_one(
                    self.connection,
                    "SELECT * FROM memory_candidates WHERE candidate_uuid = %s FOR UPDATE",
                    (proposal.proposal_id,),
                )
                if existing_candidate is None:
                    raise SemanticCoverageIdentityConflict("linked candidate row is missing")
                _assert_existing_postgres_candidate(
                    self.connection,
                    existing_candidate,
                    candidate,
                    evidence,
                )
                return {"target_uuid": proposal.proposal_id, "state": "existing"}
            existing_candidate = _fetch_one(
                self.connection,
                "SELECT * FROM memory_candidates WHERE candidate_uuid = %s FOR UPDATE",
                (proposal.proposal_id,),
            )
            if existing_candidate is None:
                self.connection.execute(
                    """
                    INSERT INTO memory_candidates (
                      candidate_uuid, processing_run_uuid, text_unit_uuid, candidate_type,
                      subject_key, predicate, object_jsonb, normalized_text,
                      source_authority, explicitness, confidence, importance,
                      sensitivity, status, rejection_reason, extraction_metadata_jsonb,
                      created_at, schema_version, prompt_execution_uuid, polarity
                    )
                    VALUES (
                      %s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,%s,%s,%s,
                      %s::jsonb,%s,1,%s,%s
                    )
                    """,
                    _postgres_candidate_values(candidate),
                )
                for item in evidence:
                    self.connection.execute(
                        """
                        INSERT INTO candidate_evidence (
                          evidence_uuid, candidate_uuid, message_uuid, text_unit_uuid,
                          annotation_uuid, route_uuid, evidence_text, start_char,
                          end_char, evidence_role, support_type, created_at, schema_version
                        )
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,1)
                        """,
                        (
                            item.evidence_uuid,
                            item.candidate_uuid,
                            item.message_uuid,
                            item.text_unit_uuid,
                            item.annotation_uuid,
                            item.route_uuid,
                            item.evidence_text,
                            item.start_char,
                            item.end_char,
                            item.evidence_role.value,
                            item.support_type.value,
                            item.created_at,
                        ),
                    )
            else:
                _assert_existing_postgres_candidate(
                    self.connection,
                    existing_candidate,
                    candidate,
                    evidence,
                )
            self.connection.execute(
                """
                UPDATE semantic_candidate_links
                SET candidate_uuid = proposal_uuid,
                    state = 'candidate_linked',
                    linked_at = now(),
                    updated_at = now()
                WHERE proposal_uuid = %s
                """,
                (proposal.proposal_id,),
            )
        return {"target_uuid": proposal.proposal_id, "state": "created"}

    def _assert_candidate_authority(self, authority: CandidateAuthorityBinding) -> None:
        row = _fetch_one(
            self.connection,
            """
            SELECT g.gate_decision_uuid, g.decision,
                   r.route_uuid, r.route_type, r.status, r.annotation_uuid
            FROM memory_gate_decisions g
            JOIN memory_signal_routes r
              ON r.route_uuid = %s
             AND r.unit_uuid = g.text_unit_uuid
            WHERE g.processing_run_uuid = %s
              AND g.text_unit_uuid = %s
            FOR UPDATE OF g, r
            """,
            (
                authority.route_uuid,
                authority.processing_run_uuid,
                authority.text_unit_uuid,
            ),
        )
        if row is None or any(
            (
                row["gate_decision_uuid"] != authority.gate_decision_uuid,
                row["decision"] != authority.gate_decision,
                row["route_uuid"] != authority.route_uuid,
                row["route_type"] != authority.route_type,
                row["status"] != authority.route_status,
                row["annotation_uuid"] != authority.annotation_uuid,
            )
        ):
            raise SemanticCoverageIdentityConflict(
                "persisted gate/route authority changed before candidate creation"
            )

    def _assert_plan_replay(
        self, plan: CoveragePlan, bindings: CoveragePersistenceBindings
    ) -> None:
        row = _fetch_one(
            self.connection,
            """
            SELECT coverage_hash, message_uuid, message_version_uuid, processing_run_uuid,
                   semantic_prompt_execution_uuid, raw_text_hash,
                   text_envelope_contract_version, semantic_contract_hash,
                   route_mapping_version, provenance_policy_version,
                   privacy_policy_version,
                   status, plan_jsonb
            FROM semantic_coverage_runs
            WHERE coverage_run_uuid = %s
            FOR UPDATE
            """,
            (coverage_run_uuid(plan.coverage_hash),),
        )
        if row is None:
            raise SemanticCoverageIdentityConflict("coverage replay row is missing")
        expected = {
            "coverage_hash": plan.coverage_hash,
            "message_uuid": plan.message_uuid,
            "message_version_uuid": bindings.message_version_uuid,
            "processing_run_uuid": plan.processing_run_uuid,
            "semantic_prompt_execution_uuid": plan.semantic_prompt_execution_uuid,
            "raw_text_hash": plan.raw_text_hash,
            "text_envelope_contract_version": bindings.text_envelope_contract_version,
            "semantic_contract_hash": plan.semantic_contract_hash,
            "route_mapping_version": bindings.route_mapping_version,
            "provenance_policy_version": bindings.provenance_policy_version,
            "privacy_policy_version": bindings.privacy_policy_version,
            "status": plan.status,
        }
        if any(row[key] != value for key, value in expected.items()):
            raise SemanticCoverageIdentityConflict("deterministic coverage replay differs")
        stored_plan = row["plan_jsonb"]
        if isinstance(stored_plan, str):
            stored_plan = json.loads(stored_plan)
        if stored_plan != plan.model_dump(mode="json"):
            raise SemanticCoverageIdentityConflict("coverage replay plan differs")
        stored_items = _fetch_all(
            self.connection,
            """
            SELECT coverage_item_uuid, semantic_unit_id, semantic_unit_fingerprint,
                   raw_start, raw_end, disposition, gate_decision_uuid, route_uuid,
                   annotation_uuid, proposal_uuid, reason_codes_jsonb
            FROM semantic_coverage_items
            WHERE coverage_run_uuid = %s
            """,
            (coverage_run_uuid(plan.coverage_hash),),
        )
        by_id = {str(item["coverage_item_uuid"]): item for item in stored_items}
        if set(by_id) != {item.coverage_item_id for item in plan.items}:
            raise SemanticCoverageIdentityConflict("coverage replay item set differs")
        for item in plan.items:
            stored = by_id[item.coverage_item_id]
            reasons = stored["reason_codes_jsonb"]
            if isinstance(reasons, str):
                reasons = json.loads(reasons)
            expected_fingerprint = (
                bindings.semantic_unit_fingerprints.get(item.semantic_unit_id)
                if item.semantic_unit_id is not None
                else None
            )
            values = (
                stored["semantic_unit_id"] == item.semantic_unit_id,
                stored["semantic_unit_fingerprint"] == expected_fingerprint,
                stored["raw_start"] == item.raw_start,
                stored["raw_end"] == item.raw_end,
                stored["disposition"] == item.disposition.value,
                stored["gate_decision_uuid"] == item.gate_decision_uuid,
                stored["route_uuid"] == item.route_uuid,
                stored["annotation_uuid"] == bindings.annotation_uuids.get(item.coverage_item_id),
                stored["proposal_uuid"] == item.proposal_id,
                reasons == list(item.reason_codes),
            )
            if not all(values):
                raise SemanticCoverageIdentityConflict("coverage replay item differs")


def _postgres_candidate_values(candidate: MemoryCandidate) -> tuple[Any, ...]:
    object_json = candidate.object_ijson or "{}"
    metadata_json = candidate.extraction_metadata_ijson or "{}"
    rejection_reason = candidate.rejection_reason_codes_ijson
    status = (
        "needs_review"
        if candidate.status is CandidateStatus.NEEDS_REVIEW
        else ("rejected" if candidate.status is CandidateStatus.REJECTED else "accepted")
    )
    return (
        candidate.candidate_uuid,
        candidate.processing_run_uuid,
        candidate.text_unit_uuid,
        candidate.candidate_type.value,
        candidate.subject_key,
        candidate.predicate,
        object_json,
        candidate.normalized_text,
        candidate.source_authority.value,
        candidate.explicitness.value,
        candidate.confidence,
        candidate.importance,
        candidate.sensitivity_class.value,
        status,
        rejection_reason,
        metadata_json,
        candidate.created_at,
        candidate.prompt_execution_uuid,
        candidate.polarity.value,
    )


def _assert_existing_postgres_candidate(
    connection: Any,
    row: Mapping[str, Any],
    candidate: MemoryCandidate,
    evidence: Sequence[CandidateEvidence],
) -> None:
    expected_values = _postgres_candidate_values(candidate)
    expected = dict(
        zip(
            (
                "candidate_uuid",
                "processing_run_uuid",
                "text_unit_uuid",
                "candidate_type",
                "subject_key",
                "predicate",
                "object_jsonb",
                "normalized_text",
                "source_authority",
                "explicitness",
                "confidence",
                "importance",
                "sensitivity",
                "status",
                "rejection_reason",
                "extraction_metadata_jsonb",
                "created_at",
                "prompt_execution_uuid",
                "polarity",
            ),
            expected_values,
            strict=True,
        )
    )
    for column, value in expected.items():
        if column == "created_at":
            continue
        stored = row[column]
        if column in {"object_jsonb", "extraction_metadata_jsonb"}:
            stored = json.loads(stored) if isinstance(stored, str) else stored
            value = json.loads(str(value))
        if stored != value:
            raise SemanticCoverageIdentityConflict(f"existing candidate differs at {column}")
    stored_evidence = {
        str(item["evidence_uuid"]): item
        for item in _fetch_all(
            connection,
            "SELECT * FROM candidate_evidence WHERE candidate_uuid = %s",
            (candidate.candidate_uuid,),
        )
    }
    if set(stored_evidence) != {item.evidence_uuid for item in evidence}:
        raise SemanticCoverageIdentityConflict(
            "existing candidate evidence set differs from proposal"
        )
    for item in evidence:
        stored = stored_evidence[item.evidence_uuid]
        expected_item = {
            "candidate_uuid": item.candidate_uuid,
            "message_uuid": item.message_uuid,
            "text_unit_uuid": item.text_unit_uuid,
            "annotation_uuid": item.annotation_uuid,
            "route_uuid": item.route_uuid,
            "evidence_text": item.evidence_text,
            "start_char": item.start_char,
            "end_char": item.end_char,
            "evidence_role": item.evidence_role.value,
            "support_type": item.support_type.value,
        }
        if any(stored[column] != value for column, value in expected_item.items()):
            raise SemanticCoverageIdentityConflict(
                "existing candidate evidence differs from proposal"
            )


def _assert_link_row(row: Mapping[str, Any], coverage_item_id: str, payload_hash: str) -> None:
    if row["coverage_item_uuid"] != coverage_item_id or row["payload_hash"] != payload_hash:
        raise SemanticCoverageIdentityConflict(
            "deterministic proposal identity replayed with different payload"
        )


def _fetch_one(connection: Any, sql: str, params: tuple[Any, ...]) -> dict[str, Any] | None:
    rows = _fetch_all(connection, sql, params)
    return rows[0] if rows else None


def _fetch_all(connection: Any, sql: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
    if not hasattr(connection, "cursor"):
        return [dict(row) for row in connection.execute(sql, params).fetchall()]
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        columns = [item.name for item in cursor.description or ()]
        return [
            dict(row) if isinstance(row, Mapping) else dict(zip(columns, row, strict=True))
            for row in cursor.fetchall()
        ]


@contextmanager
def _transaction(connection: Any) -> Any:
    raw = getattr(connection, "raw", connection)
    connection.commit()
    with raw.transaction():
        yield
