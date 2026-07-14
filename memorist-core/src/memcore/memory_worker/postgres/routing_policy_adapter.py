from __future__ import annotations

import json
from typing import Any

from memcore.memory_worker.semantic.routing_policy import (
    decide_semantic_routes,
    route_status_for_type,
)
from memcore.models import JakobsonConfidence, new_uuid, utc_now

_CONFIDENCE_VALUE = {
    JakobsonConfidence.HIGH: "0.90",
    JakobsonConfidence.MEDIUM: "0.75",
    JakobsonConfidence.LOW: "0.35",
}


def record_routes(
    self: Any,
    message_uuid: str,
    annotations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Persist Full/PostgreSQL routes using the shared PR4-D route policy."""

    for annotation in annotations:
        secondary = _secondary(annotation.get("secondary_functions_jsonb"))
        decisions = decide_semantic_routes(
            text=_route_text(annotation),
            dominant_function=str(annotation["dominant_function"]),
            secondary_functions=secondary,
            receiver_hint=_string_or_none(annotation.get("receiver_value")),
            context_hint=_string_or_none(annotation.get("context_value")),
        )
        for decision in decisions:
            route_uuid = new_uuid()
            status = route_status_for_type(decision.route_type)
            self.connection.execute(
                """
                INSERT INTO memory_signal_routes (route_uuid, annotation_uuid, message_uuid, unit_uuid, dominant_function, secondary_functions_jsonb, route_type,
                  extractor_id, priority, confidence, reason, status, created_at, schema_version)
                VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,%s,%s,1)
                ON CONFLICT (annotation_uuid, route_type, extractor_id) DO NOTHING
                """,
                (
                    route_uuid,
                    annotation["annotation_uuid"],
                    message_uuid,
                    annotation["unit_uuid"],
                    str(annotation["dominant_function"]),
                    json.dumps(secondary),
                    decision.route_type.value,
                    decision.extractor_id,
                    decision.priority,
                    _postgres_confidence(decision.confidence),
                    decision.reason,
                    status.value,
                    utc_now(),
                ),
            )
    return [
        dict(row)
        for row in self.connection.execute(
            "SELECT * FROM memory_signal_routes WHERE message_uuid = %s", (message_uuid,)
        ).fetchall()
    ]


def _postgres_confidence(confidence: JakobsonConfidence) -> str:
    return _CONFIDENCE_VALUE.get(confidence, "0.50")


def _secondary(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list | tuple | set):
        return [str(item) for item in raw]
    if isinstance(raw, str):
        try:
            loaded = json.loads(raw)
        except json.JSONDecodeError:
            return []
        if isinstance(loaded, list):
            return [str(item) for item in loaded]
    return []


def _route_text(annotation: dict[str, Any]) -> str:
    return " ".join(
        value
        for value in (
            _string_or_none(annotation.get("sentence_text")),
            _string_or_none(annotation.get("message_value")),
            _string_or_none(annotation.get("context_value")),
            _string_or_none(annotation.get("receiver_value")),
            _string_or_none(annotation.get("code_value")),
        )
        if value
    )


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)
