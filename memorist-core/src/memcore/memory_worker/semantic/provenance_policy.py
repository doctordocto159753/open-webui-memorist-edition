from __future__ import annotations

from dataclasses import dataclass

from memcore.models import (
    CandidateStatus,
    Explicitness,
    MemorySignalRouteType,
    SourceAuthority,
)

PROVENANCE_POLICY_VERSION = "pr4d-provenance-policy-v1"

_FACTUAL_TOOL_ROUTES = {
    MemorySignalRouteType.JIRA_CONFIGURATION,
    MemorySignalRouteType.PROCESS_FACT,
    MemorySignalRouteType.PROJECT_CONTEXT,
    MemorySignalRouteType.RESOURCE_REFERENCE,
}
_SYSTEM_DIRECTIVE_ROUTES = {
    MemorySignalRouteType.PROMPT_INSTRUCTION,
    MemorySignalRouteType.STYLE_POLICY,
    MemorySignalRouteType.TASK_CONSTRAINT,
    MemorySignalRouteType.TERMINOLOGY_RULE,
    MemorySignalRouteType.WORKFLOW_POLICY,
}
_REVIEW_ROUTES = {
    MemorySignalRouteType.MANUAL_REVIEW,
    MemorySignalRouteType.PRIVACY_REVIEW,
}
_ASSISTANT_ARTIFACT_ROUTES = {
    MemorySignalRouteType.PROJECT_CONTEXT,
    MemorySignalRouteType.PROCESS_FACT,
    MemorySignalRouteType.RESOURCE_REFERENCE,
}


@dataclass(frozen=True)
class CandidateProvenanceDecision:
    source_authority: SourceAuthority
    explicitness: Explicitness
    status: CandidateStatus
    allows_candidate_creation: bool
    allows_automatic_memory_creation: bool
    reason_codes: tuple[str, ...]


def decide_candidate_provenance(
    *,
    message_role: str,
    route_type: MemorySignalRouteType,
    route_status: CandidateStatus,
    imported_record: bool = False,
) -> CandidateProvenanceDecision:
    """Apply source trust without relabelling non-user text as user authority."""

    reasons: list[str] = []
    status = route_status
    allows_candidate = True
    allows_memory = status is CandidateStatus.READY_FOR_CONSOLIDATION

    if imported_record:
        authority = SourceAuthority.IMPORTED_RECORD
        explicitness = Explicitness.INFERRED
    elif message_role == "assistant":
        authority = SourceAuthority.ASSISTANT_CLAIM
        explicitness = Explicitness.INFERRED
        if route_type in _ASSISTANT_ARTIFACT_ROUTES:
            # Earlier assistant output can be a useful project artifact without
            # becoming a user fact, identity claim, preference, or directive.
            # Retrieval keeps the assistant_claim label and lower authority score.
            allows_memory = status is CandidateStatus.READY_FOR_CONSOLIDATION
            reasons.append("assistant_project_artifact")
        else:
            status = CandidateStatus.REJECTED
            allows_memory = False
            reasons.append("assistant_claim_not_user_authoritative")
    elif message_role == "tool":
        authority = SourceAuthority.TOOL_OBSERVATION
        explicitness = Explicitness.EXPLICIT
        if route_type not in _FACTUAL_TOOL_ROUTES:
            allows_candidate = False
            allows_memory = False
            reasons.append("tool_route_not_provenance_compatible")
    elif message_role == "system":
        authority = SourceAuthority.SYSTEM_INSTRUCTION
        explicitness = Explicitness.EXPLICIT
        if route_type not in _SYSTEM_DIRECTIVE_ROUTES:
            allows_candidate = False
            allows_memory = False
            reasons.append("system_route_not_provenance_compatible")
    else:
        authority = SourceAuthority.USER_EXPLICIT
        explicitness = Explicitness.EXPLICIT

    if route_type in _REVIEW_ROUTES:
        if status is not CandidateStatus.REJECTED:
            status = CandidateStatus.NEEDS_REVIEW
        allows_memory = False
        reasons.append("route_requires_manual_review")

    return CandidateProvenanceDecision(
        source_authority=authority,
        explicitness=explicitness,
        status=status,
        allows_candidate_creation=allows_candidate,
        allows_automatic_memory_creation=allows_memory and allows_candidate,
        reason_codes=tuple(reasons),
    )
