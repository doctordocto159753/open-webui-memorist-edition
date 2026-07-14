from __future__ import annotations

import json

from memcore.memory_worker.extraction.extractor import CandidateExtractor
from memcore.memory_worker.semantic import (
    ROUTE_CANDIDATE_MAPPING_VERSION,
    CandidateAuthorityContext,
    CanonicalRouteReference,
)
from memcore.models import (
    CandidateType,
    CreatorType,
    GateDecisionValue,
    LinguisticAnalysis,
    MemorySignalRouteStatus,
    MemorySignalRouteType,
    Message,
    MessageRole,
    TextUnit,
    TextUnitType,
)
from memcore.validators.ijson import dump_ijson

_MESSAGE_UUID = "00000000-0000-4000-8000-000000000001"
_SESSION_UUID = "00000000-0000-4000-8000-000000000002"
_UNIT_UUID = "00000000-0000-4000-8000-000000000003"
_LINGUISTIC_UUID = "00000000-0000-4000-8000-000000000004"
_PROCESSING_UUID = "00000000-0000-4000-8000-000000000005"
_PROMPT_UUID = "00000000-0000-4000-8000-000000000006"
_ANNOTATION_UUID = "00000000-0000-4000-8000-000000000007"
_ROUTE_UUID = "00000000-0000-4000-8000-000000000008"
_JAKOBSON_RUN_UUID = "00000000-0000-4000-8000-000000000009"


def test_lite_creates_preference_from_canonical_route_when_analyzer_abstains() -> None:
    extracted = CandidateExtractor().extract(
        _message(),
        _unit("Dark-mode answers are easier for me to read."),
        _PROCESSING_UUID,
        _analysis(abstained=True),
        authority=_authority(MemorySignalRouteType.USER_PREFERENCE),
    )

    assert len(extracted) == 1
    assert extracted[0].candidate.candidate_type is CandidateType.PREFERENCE


def test_lite_creates_constraint_without_structured_analysis() -> None:
    extracted = CandidateExtractor().extract(
        _message(),
        _unit("Use the release checklist before publishing."),
        _PROCESSING_UUID,
        None,
        authority=_authority(MemorySignalRouteType.TASK_CONSTRAINT),
    )

    assert len(extracted) == 1
    assert extracted[0].candidate.candidate_type is CandidateType.CONSTRAINT


def test_lite_gate_discard_blocks_existing_structured_analysis() -> None:
    extracted = CandidateExtractor().extract(
        _message(),
        _unit("I prefer concise answers."),
        _PROCESSING_UUID,
        _analysis(),
        authority=_authority(
            MemorySignalRouteType.USER_PREFERENCE,
            gate_decision=GateDecisionValue.DISCARD,
        ),
    )

    assert extracted == []


def test_lite_ignore_route_blocks_existing_structured_analysis() -> None:
    extracted = CandidateExtractor().extract(
        _message(),
        _unit("Hello."),
        _PROCESSING_UUID,
        _analysis(),
        authority=_authority(
            MemorySignalRouteType.IGNORE,
            route_status=MemorySignalRouteStatus.IGNORED,
        ),
    )

    assert extracted == []


def test_lite_candidate_records_complete_canonical_provenance() -> None:
    extracted = CandidateExtractor().extract(
        _message(),
        _unit("I prefer concise answers."),
        _PROCESSING_UUID,
        _analysis(),
        authority=_authority(MemorySignalRouteType.USER_PREFERENCE),
    )

    candidate = extracted[0].candidate
    metadata = json.loads(candidate.extraction_metadata_ijson or "{}")
    assert metadata == {
        **metadata,
        "semantic_authority": "jakobson",
        "gate_decision": GateDecisionValue.ANALYZE.value,
        "route_type": MemorySignalRouteType.USER_PREFERENCE.value,
        "route_status": MemorySignalRouteStatus.READY.value,
        "annotation_uuid": _ANNOTATION_UUID,
        "route_uuid": _ROUTE_UUID,
        "analysis_run_uuid": _JAKOBSON_RUN_UUID,
        "prompt_execution_uuid": _PROMPT_UUID,
        "route_mapping_version": ROUTE_CANDIDATE_MAPPING_VERSION,
        "source_authority": "user_explicit",
    }
    assert candidate.prompt_execution_uuid == _PROMPT_UUID
    assert extracted[0].evidence[0].annotation_uuid == _ANNOTATION_UUID
    assert extracted[0].evidence[0].route_uuid == _ROUTE_UUID


def _authority(
    route_type: MemorySignalRouteType,
    *,
    gate_decision: GateDecisionValue = GateDecisionValue.ANALYZE,
    route_status: MemorySignalRouteStatus = MemorySignalRouteStatus.READY,
) -> CandidateAuthorityContext:
    return CandidateAuthorityContext(
        gate_decision=gate_decision,
        requires_high_confidence_pass=False,
        selected_route=CanonicalRouteReference(
            route_uuid=_ROUTE_UUID,
            annotation_uuid=_ANNOTATION_UUID,
            route_type=route_type,
            route_status=route_status,
            priority=90,
        ),
        analysis_run_uuid=_JAKOBSON_RUN_UUID,
        prompt_execution_uuid=_PROMPT_UUID,
    )


def _message() -> Message:
    return Message(
        message_uuid=_MESSAGE_UUID,
        session_uuid=_SESSION_UUID,
        role=MessageRole.USER,
        creator_type=CreatorType.USER,
        raw_text="test",
    )


def _unit(text: str) -> TextUnit:
    return TextUnit(
        text_unit_uuid=_UNIT_UUID,
        message_uuid=_MESSAGE_UUID,
        session_uuid=_SESSION_UUID,
        unit_index=0,
        unit_type=TextUnitType.SENTENCE,
        text=text,
        start_char=0,
        end_char=len(text),
        speaker_role="user",
        content_hash="hash",
    )


def _analysis(*, abstained: bool = False) -> LinguisticAnalysis:
    return LinguisticAnalysis(
        analysis_uuid=_LINGUISTIC_UUID,
        text_unit_uuid=_UNIT_UUID,
        processing_run_uuid=_PROCESSING_UUID,
        analysis_schema_version="test",
        speech_acts_ijson=dump_ijson([]),
        jakobson_functions_ijson=dump_ijson({}),
        conceptual_nuclei_ijson=dump_ijson([]),
        entity_mentions_ijson=dump_ijson([]),
        temporal_expressions_ijson=dump_ijson([]),
        modality_ijson=dump_ijson({"negated": False}),
        memory_signals_ijson=dump_ijson({}),
        abstention_ijson=dump_ijson({"abstained": abstained}),
    )
