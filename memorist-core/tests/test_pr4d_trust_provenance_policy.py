from __future__ import annotations

from dataclasses import replace

from memcore.memory_worker.semantic import (
    PROVENANCE_POLICY_VERSION,
    CandidateAuthorityContext,
    CandidateServiceInput,
    CanonicalRouteReference,
    build_candidate_draft,
)
from memcore.models import (
    CandidateStatus,
    GateDecisionValue,
    MemorySignalRouteStatus,
    MemorySignalRouteType,
    SourceAuthority,
)


def test_user_explicit_directive_is_ready_with_user_authority() -> None:
    draft = build_candidate_draft(
        _input(role="user", route_type=MemorySignalRouteType.STYLE_POLICY)
    )

    assert draft is not None
    assert draft.source_authority is SourceAuthority.USER_EXPLICIT
    assert draft.status is CandidateStatus.READY_FOR_CONSOLIDATION
    assert draft.allows_automatic_memory_creation


def test_assistant_claim_is_rejected_and_never_labelled_as_user_fact() -> None:
    draft = build_candidate_draft(
        _input(role="assistant", route_type=MemorySignalRouteType.USER_PREFERENCE)
    )

    assert draft is not None
    assert draft.source_authority is SourceAuthority.ASSISTANT_CLAIM
    assert draft.status is CandidateStatus.REJECTED
    assert not draft.allows_automatic_memory_creation
    assert "assistant_claim_not_user_authoritative" in draft.rejection_reason_codes


def test_tool_observation_has_tool_provenance() -> None:
    draft = build_candidate_draft(
        _input(role="tool", route_type=MemorySignalRouteType.PROCESS_FACT)
    )

    assert draft is not None
    assert draft.source_authority is SourceAuthority.TOOL_OBSERVATION
    assert draft.metadata["source_authority"] == "tool_observation"


def test_system_instruction_has_system_provenance() -> None:
    draft = build_candidate_draft(
        _input(role="system", route_type=MemorySignalRouteType.PROMPT_INSTRUCTION)
    )

    assert draft is not None
    assert draft.source_authority is SourceAuthority.SYSTEM_INSTRUCTION
    assert draft.metadata["source_authority"] == "system_instruction"


def test_imported_record_is_not_relabelled_as_user_explicit() -> None:
    value = _input(role="user", route_type=MemorySignalRouteType.PROJECT_CONTEXT)
    draft = build_candidate_draft(replace(value, imported_record=True))

    assert draft is not None
    assert draft.source_authority is SourceAuthority.IMPORTED_RECORD


def test_privacy_route_requires_review_and_never_creates_normal_memory() -> None:
    draft = build_candidate_draft(
        _input(role="user", route_type=MemorySignalRouteType.PRIVACY_REVIEW)
    )

    assert draft is not None
    assert draft.status is CandidateStatus.NEEDS_REVIEW
    assert not draft.allows_automatic_memory_creation


def test_lite_full_shared_metadata_has_versioned_provenance() -> None:
    draft = build_candidate_draft(
        _input(role="user", route_type=MemorySignalRouteType.TASK_CONSTRAINT)
    )

    assert draft is not None
    assert draft.metadata["provenance_policy_version"] == PROVENANCE_POLICY_VERSION
    assert draft.metadata["source_authority"] == SourceAuthority.USER_EXPLICIT.value
    assert draft.metadata["semantic_authority"] == "jakobson"


def _input(*, role: str, route_type: MemorySignalRouteType) -> CandidateServiceInput:
    text = "Use concise answers and preserve this workflow."
    return CandidateServiceInput(
        message_uuid="message-1",
        message_role=role,
        text_unit_uuid="unit-1",
        text=text,
        start_char=0,
        end_char=len(text),
        processing_run_uuid="processing-run-1",
        authority=CandidateAuthorityContext(
            gate_decision=GateDecisionValue.ANALYZE,
            requires_high_confidence_pass=False,
            selected_route=CanonicalRouteReference(
                route_uuid="route-1",
                annotation_uuid="annotation-1",
                route_type=route_type,
                route_status=MemorySignalRouteStatus.READY,
                priority=90,
            ),
            analysis_run_uuid="jakobson-run-1",
            prompt_execution_uuid="prompt-1",
        ),
    )
