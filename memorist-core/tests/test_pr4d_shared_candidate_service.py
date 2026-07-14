from __future__ import annotations

from memcore.memory_worker.semantic import (
    CandidateAuthorityContext,
    CandidateDraft,
    CandidateServiceInput,
    CanonicalRouteReference,
    build_candidate_draft,
)
from memcore.models import (
    CandidateStatus,
    CandidateType,
    GateDecisionValue,
    MemorySignalRouteStatus,
    MemorySignalRouteType,
)


def test_same_preference_route_produces_same_lite_full_candidate_shape() -> None:
    lite = build_candidate_draft(_input(MemorySignalRouteType.USER_PREFERENCE))
    full = build_candidate_draft(_input(MemorySignalRouteType.USER_PREFERENCE))

    assert lite is not None and full is not None
    assert _shape(lite) == _shape(full)
    assert _shape(lite) == (
        CandidateType.PREFERENCE,
        "user",
        "preference",
        "preference:user:preference:I prefer concise answers.",
        CandidateStatus.READY_FOR_CONSOLIDATION,
    )


def test_same_constraint_route_produces_same_lite_full_candidate_shape() -> None:
    lite = build_candidate_draft(_input(MemorySignalRouteType.TASK_CONSTRAINT))
    full = build_candidate_draft(_input(MemorySignalRouteType.TASK_CONSTRAINT))

    assert lite is not None and full is not None
    assert _shape(lite) == _shape(full)
    assert lite.candidate_type is CandidateType.CONSTRAINT
    assert lite.subject_key == "project"
    assert lite.predicate == "constraint"


def test_privacy_and_manual_review_drafts_never_allow_automatic_memory() -> None:
    for route_type in (
        MemorySignalRouteType.PRIVACY_REVIEW,
        MemorySignalRouteType.MANUAL_REVIEW,
    ):
        draft = build_candidate_draft(_input(route_type))
        assert draft is not None
        assert draft.status is CandidateStatus.NEEDS_REVIEW
        assert not draft.allows_automatic_memory_creation


def test_shared_draft_contains_persistence_neutral_metadata() -> None:
    draft = build_candidate_draft(_input(MemorySignalRouteType.TASK_CONSTRAINT))

    assert draft is not None
    assert draft.metadata["semantic_authority"] == "jakobson"
    assert draft.metadata["route_uuid"] == "route-1"
    assert draft.metadata["annotation_uuid"] == "annotation-1"
    assert draft.metadata["route_mapping_version"]
    assert draft.metadata["provenance_policy_version"]


def _input(route_type: MemorySignalRouteType) -> CandidateServiceInput:
    text = (
        "I prefer concise answers."
        if route_type is MemorySignalRouteType.USER_PREFERENCE
        else "Use the release checklist before publishing."
    )
    return CandidateServiceInput(
        message_uuid="message-1",
        message_role="user",
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
        provider_type="deterministic",
        model_name="deterministic_extraction",
    )


def _shape(draft: CandidateDraft) -> tuple[object, ...]:
    return (
        draft.candidate_type,
        draft.subject_key,
        draft.predicate,
        draft.normalized_text,
        draft.status,
    )
