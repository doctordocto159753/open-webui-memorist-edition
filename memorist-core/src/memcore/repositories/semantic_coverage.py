"""SQLite WP02 coverage commands, compatible with the single write actor."""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from memcore.memory_worker.semantic.coverage import CandidateProposal, CoveragePlan
from memcore.memory_worker.semantic_coverage_persistence import (
    CoveragePersistenceBindings,
    SemanticCoverageIdentityConflict,
    candidate_payload_hash,
    coverage_run_uuid,
    validate_candidate_binding,
)
from memcore.models import CandidateEvidence, MemoryCandidate, utc_now
from memcore.repositories.sqlite import SQLiteRepository
from memcore.storage.commands import WriteResult
from memcore.validators.ijson import dump_ijson


class SQLiteSemanticCoverageRepository:
    """Synchronous facade; every mutation is executed by a write command."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def persist_plan(
        self, plan: CoveragePlan, bindings: CoveragePersistenceBindings
    ) -> dict[str, Any]:
        return PersistCoveragePlanCommand(plan, bindings).execute(self.connection).result

    def reserve_candidate(
        self, proposal_id: str, coverage_item_id: str, payload_hash: str
    ) -> dict[str, Any]:
        return (
            ReserveSemanticCandidateCommand(proposal_id, coverage_item_id, payload_hash)
            .execute(self.connection)
            .result
        )

    def create_and_link_candidate(
        self,
        proposal: CandidateProposal,
        candidate: MemoryCandidate,
        evidence_items: Sequence[CandidateEvidence],
    ) -> dict[str, Any]:
        return (
            CreateAndLinkSemanticCandidateCommand(proposal, candidate, tuple(evidence_items))
            .execute(self.connection)
            .result
        )


@dataclass(frozen=True)
class PersistCoveragePlanCommand:
    plan: CoveragePlan
    bindings: CoveragePersistenceBindings
    command_type: str = "persist_semantic_coverage_plan"

    @property
    def idempotency_key(self) -> str:
        return self.plan.coverage_hash

    def validate_idempotent_replay(self, connection: sqlite3.Connection) -> None:
        _assert_plan_replay(connection, self.plan, self.bindings)

    def execute(self, connection: sqlite3.Connection) -> WriteResult:
        _begin_immediate(connection)
        try:
            run_uuid = coverage_run_uuid(self.plan.coverage_hash)
            existing = connection.execute(
                "SELECT 1 FROM semantic_coverage_runs WHERE coverage_run_uuid = ?",
                (run_uuid,),
            ).fetchone()
            if existing is not None:
                _assert_plan_replay(connection, self.plan, self.bindings)
                connection.commit()
                return _write_result(
                    self.command_type, "semantic_coverage_run", run_uuid, replay=True
                )
            now = utc_now()
            SQLiteRepository(connection).insert(
                "semantic_coverage_runs",
                _run_values(self.plan, self.bindings, now),
            )
            repository = SQLiteRepository(connection)
            for item in self.plan.items:
                repository.insert(
                    "semantic_coverage_items",
                    _item_values(item, run_uuid, self.bindings, now),
                )
            connection.commit()
            return _write_result(self.command_type, "semantic_coverage_run", run_uuid, replay=False)
        except Exception:
            connection.rollback()
            raise


@dataclass(frozen=True)
class ReserveSemanticCandidateCommand:
    proposal_id: str
    coverage_item_id: str
    payload_hash: str
    command_type: str = "reserve_semantic_candidate"

    @property
    def idempotency_key(self) -> str:
        return self.proposal_id

    def validate_idempotent_replay(self, connection: sqlite3.Connection) -> None:
        _assert_link_replay(connection, self.proposal_id, self.coverage_item_id, self.payload_hash)

    def execute(self, connection: sqlite3.Connection) -> WriteResult:
        _begin_immediate(connection)
        try:
            row = connection.execute(
                "SELECT * FROM semantic_candidate_links WHERE proposal_uuid = ?",
                (self.proposal_id,),
            ).fetchone()
            if row is not None:
                _assert_link_row(row, self.coverage_item_id, self.payload_hash)
                connection.commit()
                return _write_result(
                    self.command_type, "semantic_candidate_link", self.proposal_id, replay=True
                )
            item = connection.execute(
                """
                SELECT disposition, proposal_uuid
                FROM semantic_coverage_items
                WHERE coverage_item_uuid = ?
                """,
                (self.coverage_item_id,),
            ).fetchone()
            if (
                item is None
                or item["disposition"] != "durable_candidate"
                or item["proposal_uuid"] != self.proposal_id
            ):
                raise SemanticCoverageIdentityConflict(
                    "reservation does not match durable coverage"
                )
            now = utc_now()
            SQLiteRepository(connection).insert(
                "semantic_candidate_links",
                {
                    "proposal_uuid": self.proposal_id,
                    "coverage_item_uuid": self.coverage_item_id,
                    "candidate_uuid": None,
                    "payload_hash": self.payload_hash,
                    "state": "candidate_creation_attempted",
                    "attempted_at": now,
                    "linked_at": None,
                    "updated_at": now,
                    "schema_version": 1,
                },
            )
            connection.commit()
            return _write_result(
                self.command_type, "semantic_candidate_link", self.proposal_id, replay=False
            )
        except Exception:
            connection.rollback()
            raise


@dataclass(frozen=True)
class CreateAndLinkSemanticCandidateCommand:
    proposal: CandidateProposal
    candidate: MemoryCandidate
    evidence_items: tuple[CandidateEvidence, ...]
    command_type: str = "create_and_link_semantic_candidate"

    @property
    def idempotency_key(self) -> str:
        return self.proposal.proposal_id

    def _payload(self) -> tuple[tuple[CandidateEvidence, ...], str]:
        evidence = validate_candidate_binding(self.proposal, self.candidate, self.evidence_items)
        return evidence, candidate_payload_hash(self.candidate, evidence)

    def validate_idempotent_replay(self, connection: sqlite3.Connection) -> None:
        _evidence, payload_hash = self._payload()
        row = connection.execute(
            "SELECT * FROM semantic_candidate_links WHERE proposal_uuid = ?",
            (self.proposal.proposal_id,),
        ).fetchone()
        if row is None or row["state"] != "candidate_linked":
            raise SemanticCoverageIdentityConflict("candidate replay is not linked")
        _assert_link_row(row, row["coverage_item_uuid"], payload_hash)

    def execute(self, connection: sqlite3.Connection) -> WriteResult:
        evidence, payload_hash = self._payload()
        _begin_immediate(connection)
        try:
            link = connection.execute(
                "SELECT * FROM semantic_candidate_links WHERE proposal_uuid = ?",
                (self.proposal.proposal_id,),
            ).fetchone()
            if link is None:
                raise SemanticCoverageIdentityConflict("candidate must be reserved before creation")
            _assert_link_row(link, link["coverage_item_uuid"], payload_hash)
            if link["state"] == "candidate_linked":
                connection.commit()
                return _write_result(
                    self.command_type,
                    "memory_candidate",
                    self.proposal.proposal_id,
                    replay=True,
                )
            repository = SQLiteRepository(connection)
            repository.insert("memory_candidates", self.candidate.model_dump(mode="json"))
            for item in evidence:
                repository.insert("candidate_evidence", item.model_dump(mode="json"))
            now = utc_now()
            connection.execute(
                """
                UPDATE semantic_candidate_links
                SET candidate_uuid = ?, state = 'candidate_linked',
                    linked_at = ?, updated_at = ?
                WHERE proposal_uuid = ?
                """,
                (self.proposal.proposal_id, now, now, self.proposal.proposal_id),
            )
            connection.commit()
            return _write_result(
                self.command_type,
                "memory_candidate",
                self.proposal.proposal_id,
                replay=False,
            )
        except Exception:
            connection.rollback()
            raise


def _run_values(
    plan: CoveragePlan, bindings: CoveragePersistenceBindings, created_at: str
) -> dict[str, Any]:
    return {
        "coverage_run_uuid": coverage_run_uuid(plan.coverage_hash),
        "coverage_plan_version": plan.coverage_plan_version,
        "coverage_hash": plan.coverage_hash,
        "message_uuid": plan.message_uuid,
        "message_version_uuid": bindings.message_version_uuid,
        "processing_run_uuid": plan.processing_run_uuid,
        "semantic_prompt_execution_uuid": plan.semantic_prompt_execution_uuid,
        "raw_text_hash": plan.raw_text_hash,
        "text_envelope_contract_version": bindings.text_envelope_contract_version,
        "semantic_contract_hash": plan.semantic_contract_hash,
        "status": plan.status,
        "plan_ijson": dump_ijson(plan.model_dump(mode="json")),
        "warnings_ijson": dump_ijson(list(plan.warnings)),
        "created_at": created_at,
        "schema_version": 1,
    }


def _item_values(
    item: Any,
    run_uuid: str,
    bindings: CoveragePersistenceBindings,
    created_at: str,
) -> dict[str, Any]:
    fingerprint = (
        bindings.semantic_unit_fingerprints.get(item.semantic_unit_id)
        if item.semantic_unit_id is not None
        else None
    )
    if item.semantic_unit_id is not None and fingerprint is None:
        raise ValueError("every semantic coverage item requires its canonical fingerprint")
    return {
        "coverage_item_uuid": item.coverage_item_id,
        "coverage_run_uuid": run_uuid,
        "semantic_unit_id": item.semantic_unit_id,
        "semantic_unit_fingerprint": fingerprint,
        "raw_start": item.raw_start,
        "raw_end": item.raw_end,
        "disposition": item.disposition.value,
        "gate_decision_uuid": item.gate_decision_uuid,
        "route_uuid": item.route_uuid,
        "annotation_uuid": bindings.annotation_uuids.get(item.coverage_item_id),
        "proposal_uuid": item.proposal_id,
        "reason_codes_ijson": dump_ijson(list(item.reason_codes)),
        "created_at": created_at,
        "schema_version": 1,
    }


def _assert_plan_replay(
    connection: sqlite3.Connection,
    plan: CoveragePlan,
    bindings: CoveragePersistenceBindings,
) -> None:
    run_uuid = coverage_run_uuid(plan.coverage_hash)
    row = connection.execute(
        "SELECT * FROM semantic_coverage_runs WHERE coverage_run_uuid = ?", (run_uuid,)
    ).fetchone()
    if row is None:
        raise SemanticCoverageIdentityConflict("coverage replay row is missing")
    expected = _run_values(plan, bindings, str(row["created_at"]))
    _assert_columns(row, expected, ignore={"created_at"})
    rows = {
        str(item["coverage_item_uuid"]): item
        for item in connection.execute(
            "SELECT * FROM semantic_coverage_items WHERE coverage_run_uuid = ?",
            (run_uuid,),
        )
    }
    if set(rows) != {item.coverage_item_id for item in plan.items}:
        raise SemanticCoverageIdentityConflict("coverage replay item set differs")
    for item in plan.items:
        expected_item = _item_values(
            item, run_uuid, bindings, str(rows[item.coverage_item_id]["created_at"])
        )
        _assert_columns(rows[item.coverage_item_id], expected_item, ignore={"created_at"})


def _assert_link_replay(
    connection: sqlite3.Connection,
    proposal_id: str,
    coverage_item_id: str,
    payload_hash: str,
) -> None:
    row = connection.execute(
        "SELECT * FROM semantic_candidate_links WHERE proposal_uuid = ?",
        (proposal_id,),
    ).fetchone()
    if row is None:
        raise SemanticCoverageIdentityConflict("candidate reservation is missing")
    _assert_link_row(row, coverage_item_id, payload_hash)


def _assert_link_row(row: Mapping[str, Any], coverage_item_id: str, payload_hash: str) -> None:
    if row["coverage_item_uuid"] != coverage_item_id or row["payload_hash"] != payload_hash:
        raise SemanticCoverageIdentityConflict(
            "deterministic proposal identity replayed with different payload"
        )


def _assert_columns(
    row: Mapping[str, Any], expected: Mapping[str, Any], *, ignore: set[str]
) -> None:
    for key, value in expected.items():
        if key not in ignore and row[key] != value:
            raise SemanticCoverageIdentityConflict(
                f"deterministic coverage replay differs at {key}"
            )


def _begin_immediate(connection: sqlite3.Connection) -> None:
    if connection.in_transaction:
        connection.rollback()
    connection.execute("BEGIN IMMEDIATE")


def _write_result(
    command_type: str,
    target_type: str,
    target_uuid: str,
    *,
    replay: bool,
) -> WriteResult:
    return WriteResult(
        command_type=command_type,
        target_type=target_type,
        target_uuid=target_uuid,
        result={
            "target_uuid": target_uuid,
            "state": "existing" if replay else "created",
        },
        idempotent_replay=replay,
    )
