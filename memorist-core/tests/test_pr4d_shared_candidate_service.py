from __future__ import annotations

import json
from typing import Any

from memcore.memory_worker.extraction.extractor import CandidateExtractor
from memcore.memory_worker.postgres.gated_candidate_adapter import record_candidates
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
    CreatorType,
    GateDecisionValue,
    MemorySignalRouteStatus,
    MemorySignalRouteType,
    Message,
    MessageRole,
    TextUnit,
    TextUnitType,
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


def test_lite_extractor_and_full_adapter_persist_equivalent_candidate_semantics() -> None:
    text = "Do not use cloud storage."
    authority = CandidateAuthorityContext(
        gate_decision=GateDecisionValue.ANALYZE_HIGH_CONFIDENCE,
        requires_high_confidence_pass=True,
        selected_route=CanonicalRouteReference(
            route_uuid="00000000-0000-4000-8000-000000000005",
            annotation_uuid="00000000-0000-4000-8000-000000000006",
            route_type=MemorySignalRouteType.TASK_CONSTRAINT,
            route_status=MemorySignalRouteStatus.READY,
            priority=90,
        ),
        analysis_run_uuid="00000000-0000-4000-8000-000000000007",
        prompt_execution_uuid="00000000-0000-4000-8000-000000000008",
    )
    lite = CandidateExtractor().extract(
        Message(
            message_uuid="00000000-0000-4000-8000-000000000001",
            session_uuid="00000000-0000-4000-8000-000000000002",
            role=MessageRole.USER,
            creator_type=CreatorType.USER,
            raw_text=text,
        ),
        TextUnit(
            text_unit_uuid="00000000-0000-4000-8000-000000000003",
            message_uuid="00000000-0000-4000-8000-000000000001",
            session_uuid="00000000-0000-4000-8000-000000000002",
            unit_index=0,
            unit_type=TextUnitType.SENTENCE,
            text=text,
            start_char=0,
            end_char=len(text),
            speaker_role="user",
            content_hash="hash",
        ),
        "00000000-0000-4000-8000-000000000004",
        None,
        authority=authority,
        provider_type="deterministic",
        model_name="deterministic_extraction",
    )
    assert len(lite) == 1

    full = _FakePipeline()
    record_candidates(
        full,
        "00000000-0000-4000-8000-000000000004",
        {
            "message_uuid": "00000000-0000-4000-8000-000000000001",
            "session_uuid": "00000000-0000-4000-8000-000000000002",
            "role": "user",
        },
        [
            {
                "text_unit_uuid": "00000000-0000-4000-8000-000000000003",
                "text": text,
                "start_char": 0,
                "end_char": len(text),
            }
        ],
        [
            {
                "annotation_uuid": "00000000-0000-4000-8000-000000000006",
                "analysis_run_uuid": "00000000-0000-4000-8000-000000000007",
                "unit_uuid": "00000000-0000-4000-8000-000000000003",
            }
        ],
        [
            {
                "route_uuid": "00000000-0000-4000-8000-000000000005",
                "annotation_uuid": "00000000-0000-4000-8000-000000000006",
                "unit_uuid": "00000000-0000-4000-8000-000000000003",
                "route_type": MemorySignalRouteType.TASK_CONSTRAINT.value,
                "status": MemorySignalRouteStatus.READY.value,
                "priority": 90,
            }
        ],
        "00000000-0000-4000-8000-000000000008",
        "deterministic",
        model_name="deterministic_extraction",
        analyses=[],
    )
    assert len(full.connection.candidate_params) == 1

    lite_candidate = lite[0].candidate
    full_params = full.connection.candidate_params[0]
    assert (
        lite_candidate.candidate_type.value,
        lite_candidate.subject_key,
        lite_candidate.predicate,
        lite_candidate.normalized_text,
        lite_candidate.source_authority.value,
        lite_candidate.explicitness.value,
        lite_candidate.confidence,
        lite_candidate.importance,
        lite_candidate.sensitivity_class.value,
    ) == (
        full_params[3],
        full_params[4],
        full_params[5],
        full_params[7],
        full_params[8],
        full_params[9],
        full_params[10],
        full_params[11],
        full_params[12],
    )
    assert full_params[13] == "accepted"
    assert json.loads(lite_candidate.extraction_metadata_ijson or "{}") == json.loads(
        full_params[15]
    )


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
    def __init__(self) -> None:
        self.candidate_params: list[tuple[Any, ...]] = []

    def execute(self, sql: str, params: tuple[Any, ...]) -> _Result:
        if "FROM memory_gate_decisions" in sql:
            return _Result(
                many=[
                    {
                        "text_unit_uuid": "00000000-0000-4000-8000-000000000003",
                        "decision": GateDecisionValue.ANALYZE_HIGH_CONFIDENCE.value,
                        "requires_high_confidence_pass": True,
                    }
                ]
            )
        if "SELECT 1 FROM memory_candidates" in sql:
            return _Result(one=None)
        if "INSERT INTO memory_candidates" in sql:
            self.candidate_params.append(params)
            return _Result()
        if "SELECT * FROM memory_candidates" in sql:
            return _Result(many=[])
        return _Result()


class _FakePipeline:
    def __init__(self) -> None:
        self.connection = _FakeConnection()
