"""PRD-02 WP01: Lite and Full share one text-semantics authority.

Both runtimes must reach the same normalized tokens, the same receiver and
context, and the same polarity. If either backend forked into a second lexical
authority, these assertions are what would catch it.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from types import SimpleNamespace
from typing import Any, cast

import pytest

from memcore.memory_worker.analysis.provider import DeterministicStructuredAnalysisProvider
from memcore.memory_worker.analysis.schemas import AnalysisRequest
from memcore.memory_worker.extraction.extractor import CandidateExtractor
from memcore.memory_worker.jakobson.service import DeterministicJakobsonProvider
from memcore.memory_worker.postgres.gated_candidate_adapter import record_candidates
from memcore.memory_worker.postgres.pipeline import PostgresMemoryWorkerPipeline
from memcore.memory_worker.prompts.contracts import canonical_sentence_items
from memcore.memory_worker.semantic import (
    CandidateAuthorityContext,
    CanonicalRouteReference,
    resolve_semantic_factors,
)
from memcore.models import (
    CreatorType,
    GateDecisionValue,
    LinguisticAnalysis,
    MemorySignalRouteStatus,
    MemorySignalRouteType,
    Message,
    MessageRole,
    Polarity,
    TextUnit,
    TextUnitType,
    new_uuid,
)
from memcore.textsemantics import extract_polarity, normalize_text, tokenize
from memcore.validators.ijson import dump_ijson

MESSAGE_UUID = "00000000-0000-4000-8000-000000000001"
SESSION_UUID = "00000000-0000-4000-8000-000000000002"
UNIT_UUID = "00000000-0000-4000-8000-000000000003"
RUN_UUID = "00000000-0000-4000-8000-000000000004"
ROUTE_UUID = "00000000-0000-4000-8000-000000000005"
ANNOTATION_UUID = "00000000-0000-4000-8000-000000000006"
ANALYSIS_RUN_UUID = "00000000-0000-4000-8000-000000000007"
EXECUTION_UUID = "00000000-0000-4000-8000-000000000008"
ANALYSIS_UUID = "00000000-0000-4000-8000-000000000009"

PARITY_TEXTS = [
    "تصمیم گرفتیم از Jira استفاده کنیم.",
    "تیم محصول باید چک‌لیست را بخواند.",
    "توسعه‌دهنده باید تست بنویسد.",
    "We never deploy on Friday.",
    "The product team must update the Jira workflow.",
    "شما باید این پرامپت را اصلاح کنید.",
    "ما هرگز از این روش استفاده نمی‌کنیم.",
]


@pytest.mark.parametrize("text", PARITY_TEXTS)
def test_normalized_tokens_and_polarity_are_runtime_independent(text: str) -> None:
    """The service is pure, so both runtimes read the same values from it."""

    tokens = [token.text for token in tokenize(text)]

    assert tokens == [token.text for token in tokenize(normalize_text(text))]
    assert extract_polarity(text).polarity is extract_polarity(normalize_text(text)).polarity


@pytest.mark.parametrize("text", PARITY_TEXTS)
def test_lite_and_full_deterministic_jakobson_agree_on_resolved_factors(text: str) -> None:
    """Both runtimes resolve the same receiver, context, and function.

    This is the decision WP01 owns. How each runtime then renders an
    unresolved receiver into its annotation payload is a separate, pre-existing
    difference pinned by the characterization test below.
    """

    lite = canonical_sentence_items(
        DeterministicJakobsonProvider().analyze([_lite_unit(text)], text)
    )[0]
    pipeline = object.__new__(PostgresMemoryWorkerPipeline)
    full = canonical_sentence_items(
        PostgresMemoryWorkerPipeline._deterministic_jakobson_output(pipeline, [_full_unit(text)])
    )[0]

    resolved = resolve_semantic_factors(text, dominant_function=None)
    assert lite["dominant_function"] == full["dominant_function"]
    assert lite["secondary_functions"] == full["secondary_functions"]
    assert lite["function_reason"] == full["function_reason"]
    assert lite["six_factors"]["context_referent"] == full["six_factors"]["context_referent"]
    # A resolved receiver is identical in both runtimes.
    if resolved.receiver.value is not None:
        assert (
            lite["six_factors"]["receiver_addressee"] == full["six_factors"]["receiver_addressee"]
        )


def test_unresolved_receiver_rendering_differs_between_runtimes_today() -> None:
    """Characterization of a pre-existing Lite/Full difference.

    When no receiver can be resolved, Lite records ``None`` while Full
    substitutes the literal "assistant" and the whole sentence as evidence.
    This predates PRD-02 WP01 and is out of its scope: changing it would alter
    persisted Full annotations and the receiver hint that routing later reads.
    The test pins today's behaviour so a future package changes it on purpose
    rather than by accident.
    """

    text = "ما هرگز از این روش استفاده نمیکنیم."
    assert resolve_semantic_factors(text).receiver.value is None

    lite = canonical_sentence_items(
        DeterministicJakobsonProvider().analyze([_lite_unit(text)], text)
    )[0]
    pipeline = object.__new__(PostgresMemoryWorkerPipeline)
    full = canonical_sentence_items(
        PostgresMemoryWorkerPipeline._deterministic_jakobson_output(pipeline, [_full_unit(text)])
    )[0]

    assert lite["six_factors"]["receiver_addressee"]["value"] is None
    assert full["six_factors"]["receiver_addressee"]["value"] == "assistant"


def test_deterministic_analysis_modality_reports_polarity_and_the_legacy_flag() -> None:
    provider = DeterministicStructuredAnalysisProvider()

    negated = provider.analyze(_analysis_request("We never deploy on Friday."), {})
    affirmed = provider.analyze(_analysis_request("We deploy on Friday."), {})

    assert negated.modality["polarity"] == Polarity.NEGATED.value
    assert negated.modality["negated"] is True
    assert negated.modality["polarity_evidence"] == "never"
    assert affirmed.modality["polarity"] == Polarity.AFFIRMED.value
    assert affirmed.modality["negated"] is False


def test_deterministic_analysis_modality_is_token_aware() -> None:
    """The previous detector matched " not " and "never" as substrings."""

    provider = DeterministicStructuredAnalysisProvider()
    response = provider.analyze(_analysis_request("Nevertheless we deploy on Friday."), {})

    assert response.modality["negated"] is False
    assert response.modality["polarity"] == Polarity.AFFIRMED.value


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Do not use cloud storage.", Polarity.NEGATED),
        ("Use cloud storage.", Polarity.AFFIRMED),
    ],
)
def test_lite_and_full_persist_the_same_polarity(text: str, expected: Polarity) -> None:
    analysis_payload = {
        "polarity": expected.value,
        "negated": expected is Polarity.NEGATED,
    }

    lite = CandidateExtractor().extract(
        Message(
            message_uuid=MESSAGE_UUID,
            session_uuid=SESSION_UUID,
            role=MessageRole.USER,
            creator_type=CreatorType.USER,
            raw_text=text,
        ),
        TextUnit(
            text_unit_uuid=UNIT_UUID,
            message_uuid=MESSAGE_UUID,
            session_uuid=SESSION_UUID,
            unit_index=0,
            unit_type=TextUnitType.SENTENCE,
            text=text,
            start_char=0,
            end_char=len(text),
            speaker_role="user",
            content_hash="hash",
        ),
        RUN_UUID,
        _analysis(analysis_payload),
        authority=_authority(),
        provider_type="deterministic",
        model_name="deterministic_extraction",
    )

    full = _FakePipeline()
    record_candidates(
        full,
        RUN_UUID,
        {"message_uuid": MESSAGE_UUID, "session_uuid": SESSION_UUID, "role": "user"},
        [
            {
                "text_unit_uuid": UNIT_UUID,
                "text": text,
                "start_char": 0,
                "end_char": len(text),
            }
        ],
        [
            {
                "annotation_uuid": ANNOTATION_UUID,
                "analysis_run_uuid": ANALYSIS_RUN_UUID,
                "unit_uuid": UNIT_UUID,
            }
        ],
        [
            {
                "route_uuid": ROUTE_UUID,
                "annotation_uuid": ANNOTATION_UUID,
                "unit_uuid": UNIT_UUID,
                "route_type": MemorySignalRouteType.TASK_CONSTRAINT.value,
                "status": MemorySignalRouteStatus.READY.value,
                "priority": 90,
            }
        ],
        EXECUTION_UUID,
        "deterministic",
        model_name="deterministic_extraction",
        analyses=[
            {
                "analysis_uuid": ANALYSIS_UUID,
                "text_unit_uuid": UNIT_UUID,
                "modality_jsonb": json.dumps(analysis_payload),
            }
        ],
    )

    assert len(lite) == 1
    assert len(full.connection.candidate_params) == 1
    lite_candidate = lite[0].candidate
    full_params = full.connection.candidate_params[0]

    assert lite_candidate.polarity is expected
    # Polarity is the last bound parameter of the Full insert.
    assert full_params[-1] == expected.value
    assert lite_candidate.confidence == full_params[10]
    lite_metadata = json.loads(lite_candidate.extraction_metadata_ijson or "{}")
    assert lite_metadata["polarity"] == expected.value
    assert json.loads(full_params[15])["polarity"] == expected.value


def test_negated_and_affirmed_candidates_share_confidence_in_both_runtimes() -> None:
    confidences = set()
    for text, polarity in (
        ("Do not use cloud storage.", Polarity.NEGATED),
        ("Use cloud storage.", Polarity.AFFIRMED),
    ):
        payload = {"polarity": polarity.value, "negated": polarity is Polarity.NEGATED}
        full = _FakePipeline()
        record_candidates(
            full,
            RUN_UUID,
            {"message_uuid": MESSAGE_UUID, "session_uuid": SESSION_UUID, "role": "user"},
            [
                {
                    "text_unit_uuid": UNIT_UUID,
                    "text": text,
                    "start_char": 0,
                    "end_char": len(text),
                }
            ],
            [
                {
                    "annotation_uuid": ANNOTATION_UUID,
                    "analysis_run_uuid": ANALYSIS_RUN_UUID,
                    "unit_uuid": UNIT_UUID,
                }
            ],
            [
                {
                    "route_uuid": ROUTE_UUID,
                    "annotation_uuid": ANNOTATION_UUID,
                    "unit_uuid": UNIT_UUID,
                    "route_type": MemorySignalRouteType.TASK_CONSTRAINT.value,
                    "status": MemorySignalRouteStatus.READY.value,
                    "priority": 90,
                }
            ],
            EXECUTION_UUID,
            "deterministic",
            model_name="deterministic_extraction",
            analyses=[
                {
                    "analysis_uuid": ANALYSIS_UUID,
                    "text_unit_uuid": UNIT_UUID,
                    "modality_jsonb": json.dumps(payload),
                }
            ],
        )
        confidences.add(full.connection.candidate_params[0][10])

    assert len(confidences) == 1


def _lite_unit(text: str) -> Any:
    return cast(
        Any,
        SimpleNamespace(
            text_unit_uuid=UNIT_UUID,
            text=text,
            speaker_role="user",
            language_hint="fa",
            language_code="fa",
            start_char=0,
            end_char=len(text),
        ),
    )


def _full_unit(text: str) -> dict[str, Any]:
    return {
        "text_unit_uuid": UNIT_UUID,
        "text": text,
        "speaker_role": "user",
        "start_char": 0,
        "end_char": len(text),
    }


def _analysis_request(text: str) -> AnalysisRequest:
    return AnalysisRequest(text_unit_uuid=UNIT_UUID, text=text, speaker_role="user")


def _analysis(modality: Mapping[str, object]) -> LinguisticAnalysis:
    return LinguisticAnalysis(
        analysis_uuid=ANALYSIS_UUID,
        text_unit_uuid=UNIT_UUID,
        processing_run_uuid=RUN_UUID,
        analysis_schema_version="1.0",
        speech_acts_ijson=dump_ijson([]),
        jakobson_functions_ijson=dump_ijson({}),
        conceptual_nuclei_ijson=dump_ijson([]),
        entity_mentions_ijson=dump_ijson([]),
        temporal_expressions_ijson=dump_ijson([]),
        modality_ijson=dump_ijson(modality),
        memory_signals_ijson=dump_ijson([]),
        abstention_ijson=dump_ijson({"abstained": False, "reason": None}),
    )


def _authority() -> CandidateAuthorityContext:
    return CandidateAuthorityContext(
        gate_decision=GateDecisionValue.ANALYZE_HIGH_CONFIDENCE,
        requires_high_confidence_pass=True,
        selected_route=CanonicalRouteReference(
            route_uuid=ROUTE_UUID,
            annotation_uuid=ANNOTATION_UUID,
            route_type=MemorySignalRouteType.TASK_CONSTRAINT,
            route_status=MemorySignalRouteStatus.READY,
            priority=90,
        ),
        analysis_run_uuid=ANALYSIS_RUN_UUID,
        prompt_execution_uuid=EXECUTION_UUID,
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
                        "text_unit_uuid": UNIT_UUID,
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
        self.settings = SimpleNamespace()
        self.uuid = new_uuid
