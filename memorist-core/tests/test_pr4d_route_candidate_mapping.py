from __future__ import annotations

import json

from memcore.memory_worker.extraction.extractor import CandidateExtractor
from memcore.memory_worker.semantic import (
    ROUTE_CANDIDATE_MAPPING_VERSION,
    candidate_mapping_for_route,
)
from memcore.models import (
    CandidateStatus,
    CandidateType,
    CreatorType,
    LinguisticAnalysis,
    MemorySignalRouteType,
    Message,
    MessageRole,
    TextUnit,
    TextUnitType,
)
from memcore.validators.ijson import dump_ijson


def test_route_candidate_mapper_projects_route_type_to_candidate_shape() -> None:
    mapping = candidate_mapping_for_route(
        MemorySignalRouteType.USER_PREFERENCE,
        "Dark-mode answers are easier for me to read.",
        message_uuid="message-1",
    )

    assert mapping is not None
    assert mapping.candidate_type is CandidateType.PREFERENCE
    assert mapping.subject_key == "user"
    assert mapping.predicate == "preference"
    assert mapping.normalized_text.startswith("preference:user:preference:")
    assert mapping.status is CandidateStatus.READY_FOR_CONSOLIDATION


def test_route_candidate_mapper_blocks_ignore_route() -> None:
    assert candidate_mapping_for_route(MemorySignalRouteType.IGNORE, "hello") is None


def test_candidate_extractor_uses_explicit_route_mapping_when_text_classifier_would_abstain() -> None:
    extracted = CandidateExtractor().extract(
        _message(),
        _unit("Dark-mode answers are easier for me to read."),
        "run-1",
        _analysis(),
        route_type=MemorySignalRouteType.USER_PREFERENCE.value,
        route_uuid="route-1",
    )

    assert len(extracted) == 1
    candidate = extracted[0].candidate
    assert candidate.candidate_type is CandidateType.PREFERENCE
    assert candidate.subject_key == "user"
    assert candidate.predicate == "preference"
    assert candidate.normalized_text.startswith("preference:user:preference:")
    metadata = json.loads(candidate.extraction_metadata_ijson or "{}")
    assert metadata["route_mapping_version"] == ROUTE_CANDIDATE_MAPPING_VERSION
    assert metadata["route_uuid"] == "route-1"


def test_candidate_extractor_uses_route_injected_into_lite_analysis() -> None:
    analysis = _analysis(
        raw_output={
            "pr4d_selected_route": {
                "route_uuid": "route-2",
                "route_type": MemorySignalRouteType.TASK_CONSTRAINT.value,
                "route_status": "ready",
            }
        }
    )

    extracted = CandidateExtractor().extract(
        _message(),
        _unit("Use the release checklist before publishing."),
        "run-1",
        analysis,
    )

    assert len(extracted) == 1
    candidate = extracted[0].candidate
    assert candidate.candidate_type is CandidateType.CONSTRAINT
    assert candidate.subject_key == "project"
    assert candidate.predicate == "constraint"
    metadata = json.loads(candidate.extraction_metadata_ijson or "{}")
    assert metadata["route_type"] == MemorySignalRouteType.TASK_CONSTRAINT.value
    assert metadata["route_uuid"] == "route-2"


def _message() -> Message:
    return Message(
        message_uuid="00000000-0000-4000-8000-000000000001",
        session_uuid="00000000-0000-4000-8000-000000000002",
        role=MessageRole.USER,
        creator_type=CreatorType.USER,
        raw_text="Dark-mode answers are easier for me to read.",
    )


def _unit(text: str) -> TextUnit:
    return TextUnit(
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
    )


def _analysis(raw_output: dict[str, object] | None = None) -> LinguisticAnalysis:
    return LinguisticAnalysis(
        analysis_uuid="00000000-0000-4000-8000-000000000004",
        text_unit_uuid="00000000-0000-4000-8000-000000000003",
        processing_run_uuid="run-1",
        analysis_schema_version="test",
        speech_acts_ijson=dump_ijson([]),
        jakobson_functions_ijson=dump_ijson({}),
        conceptual_nuclei_ijson=dump_ijson([]),
        entity_mentions_ijson=dump_ijson([]),
        temporal_expressions_ijson=dump_ijson([]),
        modality_ijson=dump_ijson({"negated": False}),
        memory_signals_ijson=dump_ijson({}),
        abstention_ijson=dump_ijson({}),
        raw_output_ijson=dump_ijson(raw_output or {}),
    )
