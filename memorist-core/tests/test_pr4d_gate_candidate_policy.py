from __future__ import annotations

import json
from typing import Any

from memcore.memory_worker.postgres.gated_candidate_adapter import record_candidates
from memcore.memory_worker.semantic import (
    candidate_policy_for_gate_and_route,
    gate_allows_analysis,
    gate_allows_candidate_creation,
)
from memcore.models import GateDecisionValue, MemorySignalRouteStatus, MemorySignalRouteType


class _Result:
    def __init__(
        self,
        *,
        one: object | None = None,
        many: list[dict[str, Any]] | None = None,
    ) -> None:
        self.one = one
        self.many = many or []

    def fetchone(self) -> object | None:
        return self.one

    def fetchall(self) -> list[dict[str, Any]]:
        return self.many


class _FakeConnection:
    def __init__(self, gates: list[dict[str, Any]]) -> None:
        self.gates = gates
        self.candidate_params: list[tuple[Any, ...]] = []
        self.evidence_params: list[tuple[Any, ...]] = []

    def execute(self, sql: str, params: tuple[Any, ...]) -> _Result:
        if "FROM memory_gate_decisions" in sql:
            return _Result(many=self.gates)
        if "SELECT 1 FROM memory_candidates" in sql:
            return _Result(one=None)
        if "INSERT INTO memory_candidates" in sql:
            self.candidate_params.append(params)
            return _Result()
        if "INSERT INTO candidate_evidence" in sql:
            self.evidence_params.append(params)
            return _Result()
        if "SELECT * FROM memory_candidates" in sql:
            return _Result(many=[])
        return _Result()


class _FakePipeline:
    def __init__(self, gates: list[dict[str, Any]]) -> None:
        self.connection = _FakeConnection(gates)


def test_gate_policy_blocks_non_candidate_gate_decisions() -> None:
    assert not gate_allows_candidate_creation(GateDecisionValue.DISCARD)
    assert not gate_allows_candidate_creation(GateDecisionValue.RETAIN_RAW_ONLY)
    assert not gate_allows_candidate_creation(GateDecisionValue.MANUAL_REVIEW)
    assert gate_allows_analysis(GateDecisionValue.MANUAL_REVIEW)


def test_gate_policy_allows_analyze_and_high_confidence_candidates() -> None:
    analyze = candidate_policy_for_gate_and_route(
        gate_decision=GateDecisionValue.ANALYZE,
        route_type=MemorySignalRouteType.PROCESS_FACT,
        route_status=MemorySignalRouteStatus.READY,
    )
    high_confidence = candidate_policy_for_gate_and_route(
        gate_decision=GateDecisionValue.ANALYZE_HIGH_CONFIDENCE,
        route_type=MemorySignalRouteType.TASK_CONSTRAINT,
        route_status=MemorySignalRouteStatus.READY,
    )

    assert analyze.allows_candidate_creation
    assert analyze.allows_automatic_memory_creation
    assert high_confidence.allows_candidate_creation
    assert high_confidence.requires_high_confidence_pass


def test_gate_policy_blocks_ignore_route_even_when_gate_allows_analysis() -> None:
    policy = candidate_policy_for_gate_and_route(
        gate_decision=GateDecisionValue.ANALYZE,
        route_type=MemorySignalRouteType.IGNORE,
        route_status=MemorySignalRouteStatus.IGNORED,
    )

    assert not policy.allows_candidate_creation
    assert not policy.allows_automatic_memory_creation
    assert "route_ignored" in policy.reason_codes


def test_full_candidate_adapter_blocks_discarded_units_before_insert() -> None:
    pipeline = _FakePipeline(
        [
            {
                "text_unit_uuid": "unit-1",
                "decision": GateDecisionValue.DISCARD.value,
                "requires_high_confidence_pass": False,
            }
        ]
    )

    record_candidates(
        pipeline,
        "run-1",
        _message(),
        [_unit()],
        [_annotation()],
        [_route(route_type=MemorySignalRouteType.PROCESS_FACT.value)],
        "prompt-1",
        "deterministic",
    )

    assert pipeline.connection.candidate_params == []
    assert pipeline.connection.evidence_params == []


def test_full_candidate_adapter_blocks_ignored_routes_before_insert() -> None:
    pipeline = _FakePipeline(
        [
            {
                "text_unit_uuid": "unit-1",
                "decision": GateDecisionValue.ANALYZE.value,
                "requires_high_confidence_pass": False,
            }
        ]
    )

    record_candidates(
        pipeline,
        "run-1",
        _message(),
        [_unit()],
        [_annotation()],
        [
            _route(
                route_type=MemorySignalRouteType.IGNORE.value,
                status=MemorySignalRouteStatus.IGNORED.value,
            )
        ],
        "prompt-1",
        "deterministic",
    )

    assert pipeline.connection.candidate_params == []
    assert pipeline.connection.evidence_params == []


def test_full_candidate_adapter_allows_analyze_ready_route_and_metadata() -> None:
    pipeline = _FakePipeline(
        [
            {
                "text_unit_uuid": "unit-1",
                "decision": GateDecisionValue.ANALYZE_HIGH_CONFIDENCE.value,
                "requires_high_confidence_pass": True,
            }
        ]
    )

    record_candidates(
        pipeline,
        "run-1",
        _message(),
        [_unit()],
        [_annotation()],
        [_route(route_type=MemorySignalRouteType.TASK_CONSTRAINT.value)],
        "prompt-1",
        "deterministic",
    )

    assert len(pipeline.connection.candidate_params) == 1
    metadata = json.loads(pipeline.connection.candidate_params[0][6])
    assert metadata["gate_decision"] == GateDecisionValue.ANALYZE_HIGH_CONFIDENCE.value
    assert metadata["route_type"] == MemorySignalRouteType.TASK_CONSTRAINT.value
    assert metadata["requires_high_confidence_pass"] is True
    assert len(pipeline.connection.evidence_params) == 1
    assert pipeline.connection.evidence_params[0][5] == "route-1"


def _message() -> dict[str, Any]:
    return {"message_uuid": "message-1", "session_uuid": "session-1"}


def _unit() -> dict[str, Any]:
    return {
        "text_unit_uuid": "unit-1",
        "text": "Remember this project decision.",
        "start_char": 0,
        "end_char": 31,
    }


def _annotation() -> dict[str, Any]:
    return {"annotation_uuid": "annotation-1", "unit_uuid": "unit-1"}


def _route(
    *,
    route_type: str,
    status: str = MemorySignalRouteStatus.READY.value,
) -> dict[str, Any]:
    return {
        "route_uuid": "route-1",
        "unit_uuid": "unit-1",
        "route_type": route_type,
        "status": status,
    }
