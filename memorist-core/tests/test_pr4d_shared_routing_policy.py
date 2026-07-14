from __future__ import annotations

import json
from typing import Any

from memcore.memory_worker.postgres.routing_policy_adapter import record_routes
from memcore.memory_worker.routing.signal_router import SignalRouter
from memcore.memory_worker.semantic import decide_semantic_routes, route_status_for_type
from memcore.models import (
    JakobsonConfidence,
    JakobsonFunction,
    JakobsonSentenceAnnotation,
    MemorySignalRouteStatus,
    MemorySignalRouteType,
    new_uuid,
)
from memcore.validators.ijson import dump_ijson


class _FakeConnection:
    def __init__(self) -> None:
        self.insert_params: list[tuple[Any, ...]] = []

    def execute(self, sql: str, params: tuple[Any, ...]) -> "_FakeConnection":
        if "INSERT INTO memory_signal_routes" in sql:
            self.insert_params.append(params)
        return self

    def fetchall(self) -> list[dict[str, Any]]:
        return []


class _FakePipeline:
    def __init__(self) -> None:
        self.connection = _FakeConnection()


def test_shared_policy_routes_phatic_only_to_ignore() -> None:
    decisions = decide_semantic_routes(
        text="سلام",
        dominant_function=JakobsonFunction.PHATIC,
    )

    assert [decision.route_type for decision in decisions] == [MemorySignalRouteType.IGNORE]
    assert route_status_for_type(decisions[0].route_type) is MemorySignalRouteStatus.IGNORED


def test_lite_signal_router_delegates_phatic_only_to_shared_ignore_policy() -> None:
    routes = SignalRouter().route(
        _annotation(text="سلام", dominant=JakobsonFunction.PHATIC)
    )

    assert [route.route_type for route in routes] == [MemorySignalRouteType.IGNORE]
    assert routes[0].status is MemorySignalRouteStatus.IGNORED


def test_full_route_adapter_uses_shared_policy_for_phatic_only() -> None:
    pipeline = _FakePipeline()

    record_routes(
        pipeline,
        "message-1",
        [_annotation_row(text="سلام", dominant="phatic")],
    )

    assert len(pipeline.connection.insert_params) == 1
    inserted = pipeline.connection.insert_params[0]
    assert inserted[6] == MemorySignalRouteType.IGNORE.value
    assert inserted[7] == "none"
    assert inserted[11] == MemorySignalRouteStatus.IGNORED.value


def test_lite_and_full_shared_policy_match_team_instruction_routes() -> None:
    text = "The product team must update the Jira workflow."
    annotation = _annotation(
        text=text,
        dominant=JakobsonFunction.CONATIVE,
        receiver="product team",
        context="Jira workflow",
    )
    lite_types = [route.route_type for route in SignalRouter().route(annotation)]

    pipeline = _FakePipeline()
    record_routes(
        pipeline,
        "message-1",
        [
            _annotation_row(
                text=text,
                dominant="conative",
                receiver="product team",
                context="Jira workflow",
            )
        ],
    )
    full_types = [params[6] for params in pipeline.connection.insert_params]

    assert lite_types == [
        MemorySignalRouteType.WORKFLOW_POLICY,
        MemorySignalRouteType.TEAM_OBLIGATION,
    ]
    assert full_types == [route_type.value for route_type in lite_types]


def test_secondary_metalingual_function_can_drive_prompt_instruction_route() -> None:
    decisions = decide_semantic_routes(
        text="This prompt wording should be stable.",
        dominant_function=JakobsonFunction.REFERENTIAL,
        secondary_functions=[JakobsonFunction.METALINGUAL],
    )

    assert [decision.route_type for decision in decisions] == [
        MemorySignalRouteType.PROMPT_INSTRUCTION
    ]


def _annotation(
    *,
    text: str,
    dominant: JakobsonFunction,
    receiver: str | None = None,
    context: str | None = None,
) -> JakobsonSentenceAnnotation:
    return JakobsonSentenceAnnotation(
        annotation_uuid=new_uuid(),
        analysis_run_uuid=new_uuid(),
        message_uuid=new_uuid(),
        unit_uuid=new_uuid(),
        sentence_index=1,
        sentence_text=text,
        sentence_hash="hash",
        sender_value="user",
        sender_evidence=text,
        sender_confidence=JakobsonConfidence.HIGH,
        receiver_value=receiver,
        receiver_evidence=receiver,
        receiver_confidence=JakobsonConfidence.MEDIUM,
        message_value=text,
        message_evidence=text,
        message_confidence=JakobsonConfidence.HIGH,
        context_value=context,
        context_evidence=context,
        context_confidence=JakobsonConfidence.MEDIUM,
        code_value="English; chat register",
        code_evidence=text,
        code_confidence=JakobsonConfidence.MEDIUM,
        contact_channel_value="chat",
        contact_channel_evidence="captured chat message",
        contact_channel_confidence=JakobsonConfidence.MEDIUM,
        dominant_function=dominant,
        secondary_functions_ijson=dump_ijson([]),
        function_reason="test",
        notes=None,
        raw_sentence_output_ijson=dump_ijson({"text": text}),
    )


def _annotation_row(
    *,
    text: str,
    dominant: str,
    receiver: str | None = None,
    context: str | None = None,
) -> dict[str, Any]:
    return {
        "annotation_uuid": new_uuid(),
        "unit_uuid": new_uuid(),
        "dominant_function": dominant,
        "secondary_functions_jsonb": json.dumps([]),
        "sentence_text": text,
        "message_value": text,
        "receiver_value": receiver,
        "context_value": context,
        "code_value": "English; chat register",
    }
