from __future__ import annotations

import json
from typing import Any

from memcore.memory_worker.prompts.versions import (
    JAKOBSON_SENTENCE_ANALYSIS_PROMPT_ID,
)
from memcore.memory_worker.semantic.gate_policy import (
    candidate_policy_for_gate_and_route,
)
from memcore.models import (
    MemorySignalRouteStatus,
    MemorySignalRouteType,
    new_uuid,
    utc_now,
)

_EXISTING_CANDIDATE_SQL = " ".join(
    (
        "SELECT 1 FROM memory_candidates",
        "WHERE processing_run_uuid = %s",
        "AND text_unit_uuid = %s",
        "AND normalized_text = %s",
    )
)
_INSERT_CANDIDATE_SQL = """
    INSERT INTO memory_candidates (
      candidate_uuid, processing_run_uuid, text_unit_uuid, candidate_type,
      subject_key, predicate, object_jsonb, normalized_text, source_authority,
      explicitness, confidence, importance, sensitivity, status,
      rejection_reason, extraction_metadata_jsonb, created_at, schema_version,
      prompt_execution_uuid
    )
    VALUES (
      %s,%s,%s,'observation',%s,'states',%s::jsonb,%s,'user_message',
      'explicit',0.76,0.55,'normal','accepted',NULL,%s::jsonb,%s,1,%s
    )
"""
_INSERT_EVIDENCE_SQL = """
    INSERT INTO candidate_evidence (
      evidence_uuid, candidate_uuid, message_uuid, text_unit_uuid,
      annotation_uuid, route_uuid, evidence_text, start_char, end_char,
      created_at, schema_version
    )
    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,1)
"""
_SELECT_CANDIDATES_SQL = " ".join(
    (
        "SELECT * FROM memory_candidates",
        "WHERE processing_run_uuid = %s",
    )
)
_SELECT_GATES_SQL = """
    SELECT text_unit_uuid, decision, requires_high_confidence_pass
    FROM memory_gate_decisions
    WHERE processing_run_uuid = %s
"""


def record_candidates(
    self: Any,
    processing_run_uuid: str,
    message: dict[str, Any],
    units: list[dict[str, Any]],
    annotations: list[dict[str, Any]],
    routes: list[dict[str, Any]],
    prompt_execution_uuid: str,
    provider_type: str,
) -> list[dict[str, Any]]:
    """Persist Full/PostgreSQL candidates only after gate and route eligibility."""

    gate_by_unit = _gate_by_unit(self, processing_run_uuid)
    routes_by_unit = _routes_by_unit(routes)
    annotation_by_unit = {a["unit_uuid"]: a for a in annotations}
    for unit in units:
        unit_uuid = unit["text_unit_uuid"]
        route = _selected_route(routes_by_unit.get(unit_uuid, []))
        gate = gate_by_unit.get(unit_uuid)
        policy = candidate_policy_for_gate_and_route(
            gate_decision=gate.get("decision") if gate else None,
            route_type=route.get("route_type") if route else None,
            route_status=route.get("status") if route else None,
            requires_high_confidence_pass=bool(
                gate.get("requires_high_confidence_pass") if gate else False
            ),
        )
        if not policy.allows_candidate_creation:
            continue

        existing = self.connection.execute(
            _EXISTING_CANDIDATE_SQL,
            (processing_run_uuid, unit_uuid, unit["text"]),
        ).fetchone()
        if existing:
            continue

        annotation = annotation_by_unit.get(unit_uuid)
        candidate_uuid = new_uuid()
        self.connection.execute(
            _INSERT_CANDIDATE_SQL,
            (
                candidate_uuid,
                processing_run_uuid,
                unit_uuid,
                f"message:{message['message_uuid']}",
                json.dumps({"text": unit["text"]}),
                unit["text"],
                json.dumps(
                    {
                        "provider_type": provider_type,
                        "prompt_id": JAKOBSON_SENTENCE_ANALYSIS_PROMPT_ID,
                        "gate_decision": policy.gate_decision,
                        "route_type": policy.route_type,
                        "route_status": policy.route_status,
                        "requires_high_confidence_pass": policy.requires_high_confidence_pass,
                    },
                    sort_keys=True,
                ),
                utc_now(),
                prompt_execution_uuid,
            ),
        )
        self.connection.execute(
            _INSERT_EVIDENCE_SQL,
            (
                new_uuid(),
                candidate_uuid,
                message["message_uuid"],
                unit_uuid,
                annotation["annotation_uuid"] if annotation else None,
                route["route_uuid"] if route else None,
                unit["text"],
                unit["start_char"],
                unit["end_char"],
                utc_now(),
            ),
        )
    return [
        dict(row)
        for row in self.connection.execute(
            _SELECT_CANDIDATES_SQL,
            (processing_run_uuid,),
        ).fetchall()
    ]


def _gate_by_unit(self: Any, processing_run_uuid: str) -> dict[str, dict[str, Any]]:
    rows = self.connection.execute(
        _SELECT_GATES_SQL,
        (processing_run_uuid,),
    ).fetchall()
    return {str(row["text_unit_uuid"]): dict(row) for row in rows}


def _routes_by_unit(routes: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for route in routes:
        grouped.setdefault(str(route["unit_uuid"]), []).append(route)
    return grouped


def _selected_route(routes: list[dict[str, Any]]) -> dict[str, Any] | None:
    for route in routes:
        if str(route.get("status")) == MemorySignalRouteStatus.READY.value and str(
            route.get("route_type")
        ) != MemorySignalRouteType.IGNORE.value:
            return route
    return routes[0] if routes else None
