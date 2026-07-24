from __future__ import annotations

import json
from typing import Any

from memcore.memory_worker.semantic.authority import (
    CandidateAuthorityContext,
    CanonicalRouteReference,
    select_authoritative_route,
)
from memcore.memory_worker.semantic.candidate_service import (
    CandidateServiceInput,
    LinguisticCandidateComplements,
    build_candidate_draft,
)
from memcore.memory_worker.semantic.project_artifact import structured_project_artifact
from memcore.models import (
    CandidateStatus,
    GateDecisionValue,
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
      %s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,
      %s,%s,%s,%s,%s,%s,%s::jsonb,%s,1,%s
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
    import_run_uuid: str | None = None,
    model_name: str | None = None,
    analyses: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Persist shared candidate drafts; Full owns SQL, not candidate semantics."""

    gate_by_unit = _gate_by_unit(self, processing_run_uuid)
    routes_by_unit = _routes_by_unit(routes)
    annotation_by_unit = {str(item["unit_uuid"]): item for item in annotations}
    analysis_by_unit = {
        str(item["text_unit_uuid"]): item
        for item in (analyses or [])
        if item.get("text_unit_uuid") is not None
    }
    for unit in units:
        unit_uuid = str(unit["text_unit_uuid"])
        annotation = annotation_by_unit.get(unit_uuid)
        selected_route = select_authoritative_route(
            [_route_reference(item, annotation) for item in routes_by_unit.get(unit_uuid, [])]
        )
        gate = gate_by_unit.get(unit_uuid)
        authority = CandidateAuthorityContext(
            gate_decision=_gate_decision(gate.get("decision") if gate else None),
            requires_high_confidence_pass=bool(
                gate.get("requires_high_confidence_pass") if gate else False
            ),
            selected_route=selected_route,
            analysis_run_uuid=(
                str(annotation["analysis_run_uuid"])
                if annotation and annotation.get("analysis_run_uuid")
                else None
            ),
            prompt_execution_uuid=prompt_execution_uuid,
        )
        draft = build_candidate_draft(
            CandidateServiceInput(
                message_uuid=str(message["message_uuid"]),
                message_role=str(message.get("role") or "user"),
                text_unit_uuid=unit_uuid,
                text=str(unit["text"]),
                start_char=int(unit["start_char"]),
                end_char=int(unit["end_char"]),
                processing_run_uuid=processing_run_uuid,
                authority=authority,
                imported_record=import_run_uuid is not None,
                provider_type=provider_type,
                model_name=model_name,
                complements=_linguistic_complements(analysis_by_unit.get(unit_uuid)),
            )
        )
        if draft is None:
            continue

        existing = self.connection.execute(
            _EXISTING_CANDIDATE_SQL,
            (processing_run_uuid, unit_uuid, draft.normalized_text),
        ).fetchone()
        if existing:
            continue

        candidate_uuid = new_uuid()
        self.connection.execute(
            _INSERT_CANDIDATE_SQL,
            (
                candidate_uuid,
                draft.processing_run_uuid,
                draft.text_unit_uuid,
                draft.candidate_type.value,
                draft.subject_key,
                draft.predicate,
                json.dumps(draft.object_payload, sort_keys=True),
                draft.normalized_text,
                draft.source_authority.value,
                draft.explicitness.value,
                draft.confidence,
                draft.importance,
                draft.sensitivity.value,
                _postgres_status(draft.status),
                _postgres_rejection_reason(draft.rejection_reason_codes),
                json.dumps(draft.metadata, sort_keys=True),
                utc_now(),
                draft.prompt_execution_uuid,
            ),
        )
        self.connection.execute(
            _INSERT_EVIDENCE_SQL,
            (
                new_uuid(),
                candidate_uuid,
                draft.message_uuid,
                draft.text_unit_uuid,
                draft.annotation_uuid,
                draft.route_uuid,
                draft.evidence_text,
                draft.start_char,
                draft.end_char,
                utc_now(),
            ),
        )
    _record_structured_project_artifact(
        self,
        processing_run_uuid,
        message,
        units,
        prompt_execution_uuid,
    )
    return [
        dict(row)
        for row in self.connection.execute(
            _SELECT_CANDIDATES_SQL,
            (processing_run_uuid,),
        ).fetchall()
    ]


def _record_structured_project_artifact(
    self: Any,
    processing_run_uuid: str,
    message: dict[str, Any],
    units: list[dict[str, Any]],
    prompt_execution_uuid: str,
) -> None:
    if not units:
        return
    raw_text = str(message.get("raw_text") or "")
    artifact = structured_project_artifact(
        raw_text=raw_text,
        message_role=str(message.get("role") or ""),
        list_item_count=sum(str(unit.get("unit_type")) == "list_item" for unit in units),
        has_cross_session_scope=bool(
            message.get("project_uuid") or message.get("workspace_uuid")
        ),
        preceding_user_message_uuid=None,
    )
    if artifact is None or not message.get("created_at"):
        return
    preceding = self.connection.execute(
        """
        SELECT message_uuid
        FROM messages
        WHERE session_uuid = %s AND role = 'user' AND created_at <= %s
          AND message_uuid <> %s
        ORDER BY created_at DESC, message_uuid DESC
        LIMIT 1
        """,
        (
            message["session_uuid"],
            message["created_at"],
            message["message_uuid"],
        ),
    ).fetchone()
    artifact = structured_project_artifact(
        raw_text=raw_text,
        message_role=str(message.get("role") or ""),
        list_item_count=sum(str(unit.get("unit_type")) == "list_item" for unit in units),
        has_cross_session_scope=bool(
            message.get("project_uuid") or message.get("workspace_uuid")
        ),
        preceding_user_message_uuid=(
            str(preceding["message_uuid"]) if preceding is not None else None
        ),
    )
    assert artifact is not None
    unit_uuid = str(units[0]["text_unit_uuid"])
    existing = self.connection.execute(
        _EXISTING_CANDIDATE_SQL,
        (processing_run_uuid, unit_uuid, artifact.normalized_text),
    ).fetchone()
    if existing:
        return
    candidate_uuid = new_uuid()
    self.connection.execute(
        _INSERT_CANDIDATE_SQL,
        (
            candidate_uuid,
            processing_run_uuid,
            unit_uuid,
            "episode",
            f"project_artifact:{message['message_uuid']}",
            "structured_project_artifact",
            json.dumps(artifact.object_payload, ensure_ascii=False, sort_keys=True),
            artifact.normalized_text,
            artifact.source_authority.value,
            artifact.explicitness.value,
            artifact.confidence,
            artifact.importance,
            "normal",
            "accepted",
            None,
            json.dumps(artifact.metadata, ensure_ascii=False, sort_keys=True),
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
            None,
            None,
            raw_text,
            0,
            len(raw_text),
            utc_now(),
        ),
    )


def _linguistic_complements(
    analysis: dict[str, Any] | None,
) -> LinguisticCandidateComplements:
    if analysis is None:
        return LinguisticCandidateComplements(abstained=True)
    modality = _mapping(_json_value(analysis.get("modality_jsonb")))
    temporal = _json_value(analysis.get("temporal_expressions_jsonb"))
    first_temporal = temporal[0] if isinstance(temporal, list) and temporal else {}
    abstention = _mapping(_json_value(analysis.get("abstention_jsonb")))
    return LinguisticCandidateComplements(
        analysis_uuid=_optional_string(analysis.get("analysis_uuid")),
        negated=bool(modality.get("negated")),
        valid_from=_optional_string(_mapping(first_temporal).get("normalized")),
        temporal_precision=_optional_string(_mapping(first_temporal).get("precision")),
        abstained=bool(abstention.get("abstained")),
    )


def _json_value(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return None


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _optional_string(value: Any) -> str | None:
    return None if value is None else str(value)


def _postgres_status(status: CandidateStatus) -> str:
    if status is CandidateStatus.NEEDS_REVIEW:
        return "needs_review"
    if status is CandidateStatus.REJECTED:
        return "rejected"
    return "accepted"


def _postgres_rejection_reason(reason_codes: tuple[str, ...]) -> str | None:
    return json.dumps(list(reason_codes)) if reason_codes else None


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


def _route_reference(
    route: dict[str, Any],
    annotation: dict[str, Any] | None,
) -> CanonicalRouteReference:
    return CanonicalRouteReference(
        route_uuid=str(route["route_uuid"]),
        annotation_uuid=str(
            route.get("annotation_uuid")
            or (annotation.get("annotation_uuid") if annotation else "")
        ),
        route_type=MemorySignalRouteType(str(route["route_type"])),
        route_status=MemorySignalRouteStatus(str(route["status"])),
        priority=int(route.get("priority") or 0),
    )


def _gate_decision(value: Any) -> GateDecisionValue | None:
    try:
        return GateDecisionValue(str(value)) if value is not None else None
    except ValueError:
        return None
