from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from memcore.memory_worker.routing.models import RouteDecision
from memcore.memory_worker.semantic.factors import (
    ContextKind,
    HIGH_PRIORITY_INSTRUCTION,
    PRIVACY_CONTEXT,
    ReceiverKind,
    resolve_semantic_factors,
)
from memcore.models import JakobsonConfidence, JakobsonFunction, MemorySignalRouteType


PERSIAN_PROMPT_WORD = "پرامپت"


def decide_semantic_routes(
    *,
    text: str,
    dominant_function: JakobsonFunction | str,
    secondary_functions: Iterable[JakobsonFunction | str] = (),
    receiver_hint: str | None = None,
    context_hint: str | None = None,
) -> list[RouteDecision]:
    """Decide memory-signal routes from canonical Jakobson semantics.

    This policy is intentionally persistence-neutral. Lite and Full runtimes
    should call it and then adapt the returned decisions to their storage model.
    """

    dominant = _dominant_function(dominant_function)
    secondary = _secondary_function_values(secondary_functions)
    factors = resolve_semantic_factors(
        text,
        dominant_function=dominant,
        receiver_hint=receiver_hint,
        context_hint=context_hint,
    )
    receiver_kind = factors.receiver.kind
    context_kind = factors.context.kind

    if dominant is JakobsonFunction.PHATIC and not secondary:
        return [_ignore_route()]

    if context_kind == ContextKind.PRIVACY.value or PRIVACY_CONTEXT.search(text):
        return [
            RouteDecision(
                route_type=MemorySignalRouteType.PRIVACY_REVIEW,
                extractor_id="privacy_review_extractor.v1",
                priority=95,
                confidence=JakobsonConfidence.HIGH,
                reason="Sensitive or secret-like material requires privacy review.",
            )
        ]

    if dominant is JakobsonFunction.CONATIVE:
        return _conative_routes(text=text, receiver_kind=receiver_kind)

    if dominant is JakobsonFunction.REFERENTIAL:
        referential = _referential_routes(context_kind=context_kind)
        if referential:
            return referential

    if dominant is JakobsonFunction.METALINGUAL or JakobsonFunction.METALINGUAL.value in secondary:
        return [_metalingual_route(text=text, context_kind=context_kind)]

    if dominant is JakobsonFunction.EMOTIVE:
        return [_emotive_route(context_kind=context_kind)]

    if dominant is JakobsonFunction.POETIC or context_kind == ContextKind.POETIC.value:
        return [_style_route()]

    if dominant is JakobsonFunction.PHATIC:
        return [_ignore_route()]

    return [
        RouteDecision(
            route_type=MemorySignalRouteType.MANUAL_REVIEW,
            extractor_id="manual_review.v1",
            priority=20,
            confidence=JakobsonConfidence.LOW,
            reason="No deterministic routing rule matched confidently.",
        )
    ]


def _conative_routes(*, text: str, receiver_kind: str) -> list[RouteDecision]:
    if receiver_kind == ReceiverKind.TEAM.value:
        return [
            RouteDecision(
                route_type=MemorySignalRouteType.WORKFLOW_POLICY,
                extractor_id="workflow_policy_extractor.v1",
                priority=90,
                confidence=JakobsonConfidence.HIGH,
                reason="Conative sentence addresses team/project behavior.",
            ),
            RouteDecision(
                route_type=MemorySignalRouteType.TEAM_OBLIGATION,
                extractor_id="team_obligation_extractor.v1",
                priority=85,
                confidence=JakobsonConfidence.MEDIUM,
                reason="Instruction implies an obligation for the team.",
            ),
        ]
    if receiver_kind == ReceiverKind.AI.value:
        route_type = (
            MemorySignalRouteType.PROMPT_INSTRUCTION
            if HIGH_PRIORITY_INSTRUCTION.search(text)
            else MemorySignalRouteType.TASK_CONSTRAINT
        )
        return [
            RouteDecision(
                route_type=route_type,
                extractor_id=f"{route_type.value}_extractor.v1",
                priority=90 if HIGH_PRIORITY_INSTRUCTION.search(text) else 75,
                confidence=JakobsonConfidence.HIGH,
                reason="Conative sentence instructs the AI/assistant.",
            )
        ]
    return [
        RouteDecision(
            route_type=MemorySignalRouteType.TASK_CONSTRAINT,
            extractor_id="task_constraint_extractor.v1",
            priority=75,
            confidence=JakobsonConfidence.MEDIUM,
            reason="Conative sentence constrains future behavior.",
        )
    ]


def _referential_routes(*, context_kind: str) -> list[RouteDecision]:
    if context_kind == ContextKind.JIRA.value:
        return [
            RouteDecision(
                route_type=MemorySignalRouteType.JIRA_CONFIGURATION,
                extractor_id="jira_configuration_extractor.v1",
                priority=80,
                confidence=JakobsonConfidence.HIGH,
                reason="Referential sentence describes Jira configuration or process.",
            )
        ]
    if context_kind == ContextKind.PROCESS.value:
        return [
            RouteDecision(
                route_type=MemorySignalRouteType.PROCESS_FACT,
                extractor_id="process_fact_extractor.v1",
                priority=70,
                confidence=JakobsonConfidence.HIGH,
                reason="Referential sentence describes project/process facts.",
            )
        ]
    if context_kind == ContextKind.RESOURCE.value:
        return [
            RouteDecision(
                route_type=MemorySignalRouteType.RESOURCE_REFERENCE,
                extractor_id="resource_reference_extractor.v1",
                priority=60,
                confidence=JakobsonConfidence.MEDIUM,
                reason="Referential sentence points to a resource.",
            )
        ]
    return []


def _metalingual_route(*, text: str, context_kind: str) -> RouteDecision:
    prompt_context = context_kind == ContextKind.METALINGUAL.value and (
        "prompt" in text.lower() or PERSIAN_PROMPT_WORD in text
    )
    route_type = (
        MemorySignalRouteType.PROMPT_INSTRUCTION
        if prompt_context
        else MemorySignalRouteType.TERMINOLOGY_RULE
    )
    return RouteDecision(
        route_type=route_type,
        extractor_id=f"{route_type.value}_extractor.v1",
        priority=75,
        confidence=JakobsonConfidence.HIGH,
        reason="Metalingual sentence defines wording, terms, or prompt language.",
    )


def _emotive_route(*, context_kind: str) -> RouteDecision:
    route_type = (
        MemorySignalRouteType.USER_PREFERENCE
        if context_kind == ContextKind.EMOTIVE.value
        else MemorySignalRouteType.EMOTIONAL_STANCE
    )
    return RouteDecision(
        route_type=route_type,
        extractor_id=f"{route_type.value}_extractor.v1",
        priority=65,
        confidence=JakobsonConfidence.MEDIUM,
        reason="Emotive sentence expresses sender stance or preference.",
    )


def _style_route() -> RouteDecision:
    return RouteDecision(
        route_type=MemorySignalRouteType.STYLE_POLICY,
        extractor_id="style_policy_extractor.v1",
        priority=55,
        confidence=JakobsonConfidence.MEDIUM,
        reason="Poetic/stylistic sentence foregrounds style or branding.",
    )


def _ignore_route() -> RouteDecision:
    return RouteDecision(
        route_type=MemorySignalRouteType.IGNORE,
        extractor_id="none",
        priority=0,
        confidence=JakobsonConfidence.LOW,
        reason="Phatic contact-management sentence has no durable memory signal.",
    )


def _dominant_function(value: JakobsonFunction | str) -> JakobsonFunction:
    if isinstance(value, JakobsonFunction):
        return value
    try:
        return JakobsonFunction(str(value))
    except ValueError:
        return JakobsonFunction.REFERENTIAL


def _secondary_function_values(values: Iterable[JakobsonFunction | str] | Any) -> set[str]:
    if values is None:
        return set()
    if isinstance(values, str):
        values = [values]
    result: set[str] = set()
    for value in values:
        if isinstance(value, JakobsonFunction):
            result.add(value.value)
        else:
            result.add(str(value))
    return result
