from __future__ import annotations

from collections.abc import Callable
from typing import Any

from memcore.memory_worker.semantic.gate_policy import candidate_policy_for_gate_and_route
from memcore.models import LinguisticAnalysis

_GetForUnit = Callable[[Any, str, str], LinguisticAnalysis | None]

_GATE_SQL = """
    SELECT decision, requires_high_confidence_pass
    FROM memory_gate_decisions
    WHERE text_unit_uuid = ? AND processing_run_uuid = ?
    ORDER BY created_at DESC, gate_decision_uuid DESC
    LIMIT 1
"""
_ROUTE_SQL = """
    SELECT msr.route_type, msr.status
    FROM memory_signal_routes msr
    JOIN jakobson_sentence_annotations jsa
      ON jsa.annotation_uuid = msr.annotation_uuid
    WHERE jsa.unit_uuid = ?
    ORDER BY
      CASE WHEN msr.status = 'ready' AND msr.route_type <> 'ignore' THEN 0 ELSE 1 END,
      msr.priority DESC,
      msr.created_at,
      msr.route_uuid
    LIMIT 1
"""


def install_lite_gate_candidate_guard(repository_type: type[Any]) -> None:
    """Gate Lite analysis retrieval before the candidate extraction boundary."""

    original: _GetForUnit = repository_type.get_for_unit
    if getattr(original, "__pr4d_gate_candidate_guard__", False):
        return

    def guarded_get_for_unit(
        self: Any,
        text_unit_uuid: str,
        processing_run_uuid: str,
    ) -> LinguisticAnalysis | None:
        gate = self.connection.execute(
            _GATE_SQL,
            (text_unit_uuid, processing_run_uuid),
        ).fetchone()
        route = self.connection.execute(_ROUTE_SQL, (text_unit_uuid,)).fetchone()
        policy = candidate_policy_for_gate_and_route(
            gate_decision=_value(gate, "decision"),
            route_type=_value(route, "route_type"),
            route_status=_value(route, "status"),
            requires_high_confidence_pass=bool(
                _value(gate, "requires_high_confidence_pass") or False
            ),
        )
        if not policy.allows_candidate_creation:
            return None
        return original(self, text_unit_uuid, processing_run_uuid)

    setattr(guarded_get_for_unit, "__pr4d_gate_candidate_guard__", True)
    repository_type.get_for_unit = guarded_get_for_unit


def _value(row: Any, key: str) -> Any:
    if row is None:
        return None
    return row[key]
