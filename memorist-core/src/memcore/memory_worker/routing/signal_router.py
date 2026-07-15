from __future__ import annotations

from memcore.memory_worker.routing.models import RouteDecision
from memcore.memory_worker.semantic.routing_policy import (
    decide_semantic_routes,
    route_status_for_type,
)
from memcore.models import JakobsonSentenceAnnotation, MemorySignalRoute
from memcore.validators.ijson import load_ijson


class SignalRouter:
    def route(self, annotation: JakobsonSentenceAnnotation) -> list[MemorySignalRoute]:
        decisions = self.decide(annotation)
        routes = []
        for decision in decisions:
            status = route_status_for_type(decision.route_type)
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
        return decide_semantic_routes(
            text=_annotation_text(annotation),
            dominant_function=annotation.dominant_function,
            secondary_functions=_secondary(annotation),
            receiver_hint=annotation.receiver_value,
            context_hint=annotation.context_value,
        )


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
