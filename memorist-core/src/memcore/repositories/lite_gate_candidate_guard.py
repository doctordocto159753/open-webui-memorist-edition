from __future__ import annotations

from collections.abc import Callable
from typing import Any

from memcore.memory_worker.semantic.gate_policy import candidate_policy_for_gate_and_route
from memcore.models import LinguisticAnalysis
from memcore.validators.ijson import dump_ijson, load_ijson

_GetForUnit = Callable[[Any, str, str], LinguisticAnalysis | None]
_INSTALLED_REPOSITORIES: set[type[Any]] = set()

_GATE_SQL = """
    SELECT decision, requires_high_confidence_pass
    FROM memory_gate_decisions
    WHERE text_unit_uuid = ? AND processing_run_uuid = ?
    ORDER BY created_at DESC, gate_decision_uuid DESC
    LIMIT 1
"""
_ROUTE_SQL = """
    SELECT msr.route_uuid, msr.annotation_uuid, msr.route_type, msr.status
    FROM memory_signal_routes msr
    JOIN jakobson_sentence_annotations jsa
      ON jsa.annotation_uuid = msr.annotation_uuid
    JOIN jakobson_analysis_runs jar
      ON jar.analysis_run_uuid = jsa.analysis_run_uuid
    WHERE jsa.unit_uuid = ?
    ORDER BY
      jar.created_at DESC,
      CASE WHEN msr.status = 'ready' AND msr.route_type <> 'ignore' THEN 0 ELSE 1 END,
      msr.priority DESC,
      msr.created_at,
      msr.route_uuid
    LIMIT 1
"""


def install_lite_gate_candidate_guard(repository_type: type[Any]) -> None:
    """Gate Lite analysis retrieval before the candidate extraction boundary."""

    if repository_type in _INSTALLED_REPOSITORIES:
        return
    original: _GetForUnit = repository_type.get_for_unit

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
        analysis = original(self, text_unit_uuid, processing_run_uuid)
        if analysis is None or route is None:
            return analysis
        return analysis.model_copy(
            update={"raw_output_ijson": _raw_output_with_selected_route(analysis, route)}
        )

    repository_type.get_for_unit = guarded_get_for_unit
    _INSTALLED_REPOSITORIES.add(repository_type)


def _raw_output_with_selected_route(analysis: LinguisticAnalysis, route: Any) -> str:
    loaded = load_ijson(analysis.raw_output_ijson)
    raw_output = loaded if isinstance(loaded, dict) else {}
    raw_output["pr4d_selected_route"] = {
        "route_uuid": _value(route, "route_uuid"),
        "annotation_uuid": _value(route, "annotation_uuid"),
        "route_type": _value(route, "route_type"),
        "route_status": _value(route, "status"),
    }
    return dump_ijson(raw_output)


def _value(row: Any, key: str) -> Any:
    if row is None:
        return None
    return row[key]
