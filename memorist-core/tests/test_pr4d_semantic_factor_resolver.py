from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

from memcore.memory_worker.jakobson.service import DeterministicJakobsonProvider
from memcore.memory_worker.postgres.pipeline import PostgresMemoryWorkerPipeline
from memcore.memory_worker.routing.signal_router import SignalRouter
from memcore.memory_worker.semantic import (
    ContextKind,
    ReceiverKind,
    resolve_semantic_factors,
)
from memcore.models import (
    JakobsonConfidence,
    JakobsonFunction,
    JakobsonSentenceAnnotation,
    MemorySignalRouteType,
    new_uuid,
)
from memcore.validators.ijson import dump_ijson


def _annotation(
    *,
    text: str,
    dominant: JakobsonFunction,
    receiver: str | None = None,
    context: str | None = None,
    secondary: tuple[JakobsonFunction, ...] = (),
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
        secondary_functions_ijson=dump_ijson([item.value for item in secondary]),
        function_reason="test",
        notes=None,
        raw_sentence_output_ijson=dump_ijson({"text": text}),
    )


def test_shared_resolver_detects_receiver_and_context_from_text_and_hints() -> None:
    direct = resolve_semantic_factors(
        "The product team must update the Jira workflow.",
        dominant_function=JakobsonFunction.CONATIVE,
    )
    hinted = resolve_semantic_factors(
        "Update the board.",
        dominant_function=JakobsonFunction.CONATIVE,
        receiver_hint="product team",
        context_hint="Jira workflow",
    )
    persian = resolve_semantic_factors(
        "شما باید این پرامپت را اصلاح کنید.",
        dominant_function=JakobsonFunction.CONATIVE,
    )

    assert direct.receiver.kind == ReceiverKind.TEAM.value
    assert direct.context.kind == ContextKind.JIRA.value
    assert hinted.receiver.kind == ReceiverKind.TEAM.value
    assert hinted.context.kind == ContextKind.JIRA.value
    assert persian.receiver.kind == ReceiverKind.AI.value
    assert persian.context.kind == ContextKind.METALINGUAL.value


def test_deterministic_jakobson_provider_uses_shared_factor_resolver() -> None:
    text = "The product team must update the Jira workflow."
    unit = cast(
        Any,
        SimpleNamespace(
            text_unit_uuid=new_uuid(),
            text=text,
            speaker_role="user",
            language_hint="en",
            language_code="en",
            start_char=0,
            end_char=len(text),
        ),
    )

    output = DeterministicJakobsonProvider().analyze([unit], text)
    sentence = output["sentences"][0]

    assert sentence["six_factors"]["receiver_addressee"] == {
        "value": "Product Team",
        "evidence": "product team",
        "confidence": "high",
    }
    assert sentence["six_factors"]["context_referent"] == {
        "value": "Jira workflow",
        "evidence": "Jira",
        "confidence": "high",
    }


def test_postgres_deterministic_fallback_uses_shared_factor_resolver() -> None:
    text = "The product team must update the Jira workflow."
    pipeline = object.__new__(PostgresMemoryWorkerPipeline)

    output = pipeline._deterministic_jakobson_output([
        {
            "text_unit_uuid": new_uuid(),
            "text": text,
            "start_char": 0,
            "end_char": len(text),
        }
    ])
    sentence = output["sentences"][0]

    assert sentence["six_factors"]["receiver_addressee"] == {
        "value": "Product Team",
        "evidence": "product team",
        "confidence": "high",
    }
    assert sentence["six_factors"]["context_referent"] == {
        "value": "Jira workflow",
        "evidence": "Jira",
        "confidence": "high",
    }
    assert output["overall_summary"]["main_receiver"] == "Product Team"
    assert output["overall_summary"]["main_context"] == "Jira workflow"


def test_signal_router_uses_shared_receiver_resolution_for_hints() -> None:
    annotation = _annotation(
        text="Update the rollout checklist.",
        dominant=JakobsonFunction.CONATIVE,
        receiver="product team",
        context="project workflow",
    )

    route_types = [route.route_type for route in SignalRouter().route(annotation)]

    assert route_types == [
        MemorySignalRouteType.WORKFLOW_POLICY,
        MemorySignalRouteType.TEAM_OBLIGATION,
    ]


def test_signal_router_uses_shared_context_resolution_for_hints() -> None:
    annotation = _annotation(
        text="The board has one status column.",
        dominant=JakobsonFunction.REFERENTIAL,
        context="Jira workflow",
    )

    route_types = [route.route_type for route in SignalRouter().route(annotation)]

    assert route_types == [MemorySignalRouteType.JIRA_CONFIGURATION]


def test_signal_router_still_routes_phatic_ignore_after_resolver_wiring() -> None:
    annotation = _annotation(text="سلام", dominant=JakobsonFunction.PHATIC)

    routes = SignalRouter().route(annotation)

    assert [route.route_type for route in routes] == [MemorySignalRouteType.IGNORE]
    assert routes[0].status.value == "ignored"
