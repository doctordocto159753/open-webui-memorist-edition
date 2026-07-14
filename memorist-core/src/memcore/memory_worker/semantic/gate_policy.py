from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from memcore.models import GateDecisionValue, MemorySignalRouteStatus, MemorySignalRouteType


@dataclass(frozen=True)
class GateCandidatePolicy:
    gate_decision: str | None
    route_type: str | None
    route_status: str | None
    allows_analysis: bool
    allows_candidate_creation: bool
    allows_automatic_memory_creation: bool
    requires_high_confidence_pass: bool
    reason_codes: tuple[str, ...]


_ANALYSIS_GATE_VALUES = {
    GateDecisionValue.ANALYZE,
    GateDecisionValue.ANALYZE_HIGH_CONFIDENCE,
    GateDecisionValue.MANUAL_REVIEW,
}
_CANDIDATE_GATE_VALUES = {
    GateDecisionValue.ANALYZE,
    GateDecisionValue.ANALYZE_HIGH_CONFIDENCE,
}


def gate_allows_analysis(decision: GateDecisionValue | str | None) -> bool:
    return _gate_value(decision) in _ANALYSIS_GATE_VALUES


def gate_allows_candidate_creation(decision: GateDecisionValue | str | None) -> bool:
    return _gate_value(decision) in _CANDIDATE_GATE_VALUES


def candidate_policy_for_gate_and_route(
    *,
    gate_decision: GateDecisionValue | str | None,
    route_type: MemorySignalRouteType | str | None = None,
    route_status: MemorySignalRouteStatus | str | None = None,
    requires_high_confidence_pass: bool = False,
) -> GateCandidatePolicy:
    """Decide whether an analyzed text unit may create candidates and memories.

    Task 05 semantics:
    - discard / retain_raw_only: no analysis-driven candidate or memory;
    - manual_review: analysis/review may exist, but no automatic candidate/memory;
    - analyze: automatic candidate creation may proceed;
    - analyze_high_confidence: automatic candidate creation may proceed, marked as
      high-confidence-gated for later extraction/consolidation tasks;
    - ignore routes or ignored statuses block candidate creation regardless of gate.
    """

    gate = _gate_value(gate_decision)
    route_type_value = _route_type_value(route_type)
    route_status_value = _route_status_value(route_status)
    reasons: list[str] = []

    allows_analysis = gate in _ANALYSIS_GATE_VALUES
    allows_candidates = gate in _CANDIDATE_GATE_VALUES
    allows_memory = allows_candidates

    if gate is None:
        reasons.append("missing_gate_decision")
        allows_analysis = False
        allows_candidates = False
        allows_memory = False
    elif gate is GateDecisionValue.DISCARD:
        reasons.append("gate_discard")
    elif gate is GateDecisionValue.RETAIN_RAW_ONLY:
        reasons.append("gate_retain_raw_only")
    elif gate is GateDecisionValue.MANUAL_REVIEW:
        reasons.append("gate_manual_review_blocks_automatic_candidates")
    elif gate is GateDecisionValue.ANALYZE_HIGH_CONFIDENCE or requires_high_confidence_pass:
        reasons.append("gate_requires_high_confidence_pass")

    if _route_blocks_candidate_creation(route_type_value, route_status_value):
        reasons.append("route_ignored")
        allows_candidates = False
        allows_memory = False

    if not allows_candidates and not reasons:
        reasons.append("candidate_creation_not_allowed")

    return GateCandidatePolicy(
        gate_decision=gate.value if gate is not None else None,
        route_type=route_type_value,
        route_status=route_status_value,
        allows_analysis=allows_analysis,
        allows_candidate_creation=allows_candidates,
        allows_automatic_memory_creation=allows_memory,
        requires_high_confidence_pass=bool(
            requires_high_confidence_pass or gate is GateDecisionValue.ANALYZE_HIGH_CONFIDENCE
        ),
        reason_codes=tuple(reasons),
    )


def _route_blocks_candidate_creation(
    route_type: str | None,
    route_status: str | None,
) -> bool:
    return route_type == MemorySignalRouteType.IGNORE.value or route_status == MemorySignalRouteStatus.IGNORED.value


def _gate_value(value: GateDecisionValue | str | None) -> GateDecisionValue | None:
    if isinstance(value, GateDecisionValue):
        return value
    if value is None:
        return None
    try:
        return GateDecisionValue(str(value))
    except ValueError:
        return None


def _route_type_value(value: MemorySignalRouteType | str | None) -> str | None:
    if isinstance(value, MemorySignalRouteType):
        return value.value
    if value is None:
        return None
    return str(value)


def _route_status_value(value: MemorySignalRouteStatus | str | None) -> str | None:
    if isinstance(value, MemorySignalRouteStatus):
        return value.value
    if value is None:
        return None
    return str(value)
