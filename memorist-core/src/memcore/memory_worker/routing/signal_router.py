from __future__ import annotations

from memcore.memory_worker.routing.models import RouteDecision
from memcore.memory_worker.semantic.factors import (
    ContextKind,
    HIGH_PRIORITY_INSTRUCTION,
    PRIVACY_CONTEXT,
    ReceiverKind,
    resolve_semantic_factors,
)
from memcore.models import (
    JakobsonConfidence,
    JakobsonFunction,
    JakobsonSentenceAnnotation,
    MemorySignalRoute,
    MemorySignalRouteStatus,
    MemorySignalRouteType,
)
from memcore.validators.ijson import load_ijson


PERSIAN_PROMPT_WORD = "\u067e\u0631\u0627\u0645\u067e\u062a"


class SignalRouter:
    def route(self, annotation: JakobsonSentenceAnnotation) -> list[MemorySignalRoute]:
        decisions = self.decide(annotation)
        routes = []
        for decision in decisions:
            status = (
                MemorySignalRouteStatus.IGNORED
                if decision.route_type is MemorySignalRouteType.IGNORE
                else MemorySignalRouteStatus.READY
            )
            routes.append(
                MemorySignalRoute(
                    annotation_uuid=annotation.annotation_uuid,
                    message_uuid=annotation.message_uuid,
                    unit_uuid=annotation.unit_uuid,
                    dominant_function=annotation.dominant_function,
                    secondary_functions_ijson=annotation.secondary_functions_ijson,
                    route_type=decision.route_type,
                    extractor_id=decision.extractor_id,
                    priority=decision.priority,
                    confidence=decision.confidence,
                    reason=decision.reason,
                    status=status,
                )
            )
        return routes

    def decide(self, annotation: JakobsonSentenceAnnotation) -> list[RouteDecision]:
        text = _annotation_text(annotation)
        secondary = _secondary(annotation)
        factors = resolve_semantic_factors(
            text,
            dominant_function=annotation.dominant_function,
            receiver_hint=annotation.receiver_value,
            context_hint=annotation.context_value,
        )
        receiver_kind = factors.receiver.kind
        context_kind = factors.context.kind

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

        if annotation.dominant_function is JakobsonFunction.CONATIVE:
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

        if annotation.dominant_function is JakobsonFunction.REFERENTIAL:
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

        if (
            annotation.dominant_function is JakobsonFunction.METALINGUAL
            or "metalingual" in secondary
        ):
            prompt_context = context_kind == ContextKind.METALINGUAL.value and (
                "prompt" in text.lower() or PERSIAN_PROMPT_WORD in text
            )
            route_type = (
                MemorySignalRouteType.PROMPT_INSTRUCTION
                if prompt_context
                else MemorySignalRouteType.TERMINOLOGY_RULE
            )
            return [
                RouteDecision(
                    route_type=route_type,
                    extractor_id=f"{route_type.value}_extractor.v1",
                    priority=75,
                    confidence=JakobsonConfidence.HIGH,
                    reason="Metalingual sentence defines wording, terms, or prompt language.",
                )
            ]

        if annotation.dominant_function is JakobsonFunction.EMOTIVE:
            route_type = (
                MemorySignalRouteType.USER_PREFERENCE
                if context_kind == ContextKind.EMOTIVE.value
                else MemorySignalRouteType.EMOTIONAL_STANCE
            )
            return [
                RouteDecision(
                    route_type=route_type,
                    extractor_id=f"{route_type.value}_extractor.v1",
                    priority=65,
                    confidence=JakobsonConfidence.MEDIUM,
                    reason="Emotive sentence expresses sender stance or preference.",
                )
            ]

        if (
            annotation.dominant_function is JakobsonFunction.POETIC
            or context_kind == ContextKind.POETIC.value
        ):
            return [
                RouteDecision(
                    route_type=MemorySignalRouteType.STYLE_POLICY,
                    extractor_id="style_policy_extractor.v1",
                    priority=55,
                    confidence=JakobsonConfidence.MEDIUM,
                    reason="Poetic/stylistic sentence foregrounds style or branding.",
                )
            ]

        if annotation.dominant_function is JakobsonFunction.PHATIC:
            return [
                RouteDecision(
                    route_type=MemorySignalRouteType.IGNORE,
                    extractor_id="none",
                    priority=0,
                    confidence=JakobsonConfidence.LOW,
                    reason="Phatic contact-management sentence has no durable memory signal.",
                )
            ]

        return [
            RouteDecision(
                route_type=MemorySignalRouteType.MANUAL_REVIEW,
                extractor_id="manual_review.v1",
                priority=20,
                confidence=JakobsonConfidence.LOW,
                reason="No deterministic routing rule matched confidently.",
            )
        ]


def _annotation_text(annotation: JakobsonSentenceAnnotation) -> str:
    return " ".join(
        value
        for value in (
            annotation.sentence_text,
            annotation.message_value or "",
            annotation.context_value or "",
            annotation.receiver_value or "",
            annotation.code_value or "",
        )
        if value
    )


def _secondary(annotation: JakobsonSentenceAnnotation) -> set[str]:
    try:
        loaded = load_ijson(annotation.secondary_functions_ijson)
    except ValueError:
        return set()
    if not isinstance(loaded, list):
        return set()
    return {str(item) for item in loaded}
