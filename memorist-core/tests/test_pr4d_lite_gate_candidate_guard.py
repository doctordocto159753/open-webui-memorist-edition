from __future__ import annotations

from typing import Any

from memcore.models import GateDecisionValue, MemorySignalRouteStatus, MemorySignalRouteType
from memcore.repositories.memory_worker import LinguisticAnalysisRepository


class _Result:
    def __init__(self, row: dict[str, Any] | None) -> None:
        self.row = row

    def fetchone(self) -> dict[str, Any] | None:
        return self.row


class _GuardConnection:
    def __init__(self, *, gate: str, route_type: str, route_status: str) -> None:
        self.gate = gate
        self.route_type = route_type
        self.route_status = route_status
        self.analysis_query_reached = False

    def execute(self, sql: str, _params: tuple[object, ...]) -> _Result:
        if "FROM memory_gate_decisions" in sql:
            return _Result(
                {
                    "decision": self.gate,
                    "requires_high_confidence_pass": False,
                }
            )
        if "FROM memory_signal_routes" in sql:
            return _Result(
                {
                    "route_type": self.route_type,
                    "status": self.route_status,
                }
            )
        if "FROM linguistic_analyses" in sql:
            self.analysis_query_reached = True
        raise AssertionError(f"unexpected query: {sql}")


def test_lite_guard_blocks_manual_review_before_analysis_lookup() -> None:
    connection = _GuardConnection(
        gate=GateDecisionValue.MANUAL_REVIEW.value,
        route_type=MemorySignalRouteType.PROCESS_FACT.value,
        route_status=MemorySignalRouteStatus.READY.value,
    )
    repository = LinguisticAnalysisRepository(connection)  # type: ignore[arg-type]

    assert repository.get_for_unit("unit-1", "run-1") is None
    assert not connection.analysis_query_reached


def test_lite_guard_blocks_ignored_route_even_when_gate_allows_candidates() -> None:
    connection = _GuardConnection(
        gate=GateDecisionValue.ANALYZE.value,
        route_type=MemorySignalRouteType.IGNORE.value,
        route_status=MemorySignalRouteStatus.IGNORED.value,
    )
    repository = LinguisticAnalysisRepository(connection)  # type: ignore[arg-type]

    assert repository.get_for_unit("unit-1", "run-1") is None
    assert not connection.analysis_query_reached
