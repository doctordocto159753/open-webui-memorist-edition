from __future__ import annotations

from dataclasses import dataclass

from memcore.models import CandidateStatus, CandidateType, MemorySignalRouteType

ROUTE_CANDIDATE_MAPPING_VERSION = "pr4d-route-candidate-mapper-v1"


@dataclass(frozen=True)
class RouteCandidateMapping:
    route_type: MemorySignalRouteType
    candidate_type: CandidateType
    subject_key: str
    predicate: str
    object_value: str
    normalized_text: str
    importance: float
    status: CandidateStatus = CandidateStatus.READY_FOR_CONSOLIDATION
    rejection_reason_codes: tuple[str, ...] = ()


def candidate_mapping_for_route(
    route_type: MemorySignalRouteType | str | None,
    text: str,
    *,
    message_uuid: str | None = None,
) -> RouteCandidateMapping | None:
    """Map canonical route decisions to canonical candidate shape.

    Task 06 makes this the shared authority for route-driven candidate type,
    subject, predicate, normalized text and route-sensitive review status. The
    mapper is runtime-neutral; Lite and Full adapters are responsible only for
    persistence details.
    """

    route = _route_value(route_type)
    value = text.strip()
    if route is None or route is MemorySignalRouteType.IGNORE or not value:
        return None

    candidate_type, subject_key, predicate, importance = _candidate_profile(
        route,
        message_uuid=message_uuid,
    )
    status = CandidateStatus.READY_FOR_CONSOLIDATION
    rejection_reason_codes: tuple[str, ...] = ()
    if route in {MemorySignalRouteType.PRIVACY_REVIEW, MemorySignalRouteType.MANUAL_REVIEW}:
        status = CandidateStatus.NEEDS_REVIEW
        rejection_reason_codes = (f"{route.value}_requires_manual_review",)

    return RouteCandidateMapping(
        route_type=route,
        candidate_type=candidate_type,
        subject_key=subject_key,
        predicate=predicate,
        object_value=value,
        normalized_text=_normalized_text(candidate_type, subject_key, predicate, value),
        importance=importance,
        status=status,
        rejection_reason_codes=rejection_reason_codes,
    )


def _candidate_profile(
    route: MemorySignalRouteType,
    *,
    message_uuid: str | None,
) -> tuple[CandidateType, str, str, float]:
    if route is MemorySignalRouteType.USER_PREFERENCE:
        return CandidateType.PREFERENCE, "user", "preference", 0.70
    if route is MemorySignalRouteType.STYLE_POLICY:
        return CandidateType.STYLE, "user", "style_policy", 0.72
    if route is MemorySignalRouteType.TERMINOLOGY_RULE:
        return CandidateType.DEFINITION, "term", "meaning", 0.70
    if route in {MemorySignalRouteType.TASK_CONSTRAINT, MemorySignalRouteType.WORKFLOW_POLICY}:
        return CandidateType.CONSTRAINT, "project", "constraint", 0.85
    if route is MemorySignalRouteType.TEAM_OBLIGATION:
        return CandidateType.GOAL, "team", "obligation", 0.84
    if route is MemorySignalRouteType.PROMPT_INSTRUCTION:
        return CandidateType.INSTRUCTION, "prompt", "instruction", 0.82
    if route is MemorySignalRouteType.JIRA_CONFIGURATION:
        return CandidateType.FACT, "jira", "configuration", 0.68
    if route is MemorySignalRouteType.RESOURCE_REFERENCE:
        return CandidateType.FACT, "resource", "reference", 0.62
    if route is MemorySignalRouteType.PROJECT_CONTEXT:
        return CandidateType.FACT, "project", "context", 0.62
    if route is MemorySignalRouteType.EMOTIONAL_STANCE:
        return CandidateType.FEEDBACK, "user", "emotional_stance", 0.55
    if route is MemorySignalRouteType.PRIVACY_REVIEW:
        return CandidateType.INSTRUCTION, "privacy", "review_request", 0.90
    if route is MemorySignalRouteType.MANUAL_REVIEW:
        return CandidateType.FACT, _message_subject(message_uuid), "manual_review", 0.50
    return CandidateType.FACT, _message_subject(message_uuid), "states", 0.55


def _normalized_text(
    candidate_type: CandidateType,
    subject_key: str,
    predicate: str,
    value: str,
) -> str:
    return f"{candidate_type.value}:{subject_key}:{predicate}:{value}".strip()


def _message_subject(message_uuid: str | None) -> str:
    return f"message:{message_uuid}" if message_uuid else "message"


def _route_value(value: MemorySignalRouteType | str | None) -> MemorySignalRouteType | None:
    if isinstance(value, MemorySignalRouteType):
        return value
    if value is None:
        return None
    try:
        return MemorySignalRouteType(str(value))
    except ValueError:
        return None
