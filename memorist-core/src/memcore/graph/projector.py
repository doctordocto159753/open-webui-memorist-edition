from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


class GraphClient(Protocol):
    def graph_query(self, query: str) -> object: ...


@dataclass(frozen=True)
class GraphProjectionResult:
    projected: int = 0
    failed: int = 0
    degraded: bool = False
    last_error_sanitized: str | None = None


class FalkorGraphProjector:
    projection_version = "memorist-message-first-graph-v2"

    def __init__(self, client: GraphClient) -> None:
        self.client = client

    def project_jakobson_annotation(self, annotation: dict[str, Any]) -> None:
        query = self.jakobson_annotation_query(annotation)
        self.client.graph_query(query)

    def project_full_memory_record(self, record: dict[str, Any]) -> None:
        query = self.full_memory_record_query(record)
        self.client.graph_query(query)

    def project_memory_candidate(self, candidate: dict[str, Any]) -> None:
        query = (
            "MERGE (c:MemoryCandidate {uuid: "
            f"{_quote(candidate['candidate_uuid'])}}}) "
            f"SET c.status = {_quote(str(candidate.get('status', 'unknown')))}, "
            f"c.type = {_quote(str(candidate.get('candidate_type', 'unknown')))}"
        )
        self.client.graph_query(query)

    def project_message_semantics(self, record: dict[str, Any]) -> None:
        self.client.graph_query(self.message_semantics_query(record))

    def message_semantics_query(self, record: dict[str, Any]) -> str:
        message_uuid = str(record["message_uuid"])
        workspace_uuid = str(record["workspace_uuid"])
        project_uuid = str(record.get("project_uuid") or "")
        session_uuid = str(record["session_uuid"])
        role = str(record["source_role"])
        status = str(record["status"])
        summary = str(record.get("one_line_summary") or "")
        intent = str(record.get("summary_intent") or "unknown")
        primary_topic = str(record.get("primary_topic") or "unknown")
        secondary_topic = str(record.get("secondary_topic") or "unknown")
        categories = _as_strings(record.get("categories"))
        concepts = _as_strings(record.get("concepts"))[:5]
        entities = _as_strings(record.get("entities"))
        processes = record.get("processes") or []
        units = record.get("units") or []
        lines = [
            f"MERGE (w:Workspace {{uuid: {_quote(workspace_uuid)}}})",
            f"MERGE (s:Session {{uuid: {_quote(session_uuid)}}})",
            f"MERGE (m:Message {{uuid: {_quote(message_uuid)}}})",
            f"SET m.role = {_quote(role)}, m.status = {_quote(status)}, "
            f"m.summary = {_quote(summary)}",
            "MERGE (s)-[:HAS_MESSAGE]->(m)",
            "MERGE (m)-[:MESSAGE_IN_SESSION]->(s)",
        ]
        if project_uuid:
            lines.extend(
                [
                    f"MERGE (p:Project {{uuid: {_quote(project_uuid)}}})",
                    "MERGE (w)-[:IN_PROJECT]->(p)",
                    "MERGE (p)-[:HAS_SESSION]->(s)",
                    "MERGE (m)-[:MESSAGE_IN_PROJECT]->(p)",
                ]
            )
        lines.extend(
            [
                f"MERGE (i:Intent {{label: {_quote(intent)}}})",
                "MERGE (m)-[:HAS_INTENT]->(i)",
                f"MERGE (pt:Topic {{label: {_quote(primary_topic)}}})",
                "MERGE (m)-[:HAS_PRIMARY_TOPIC]->(pt)",
                f"MERGE (st:Topic {{label: {_quote(secondary_topic)}}})",
                "MERGE (m)-[:HAS_SECONDARY_TOPIC]->(st)",
            ]
        )
        for index, category in enumerate(categories):
            lines.extend(
                [
                    f"MERGE (mt{index}:MessageType {{name: {_quote(category)}}})",
                    f"MERGE (m)-[:HAS_MESSAGE_TYPE]->(mt{index})",
                ]
            )
        for index, concept in enumerate(concepts):
            lines.extend(
                [
                    f"MERGE (c{index}:Concept {{label: {_quote(concept)}}})",
                    f"MERGE (m)-[:HAS_CONCEPT_TAG]->(c{index})",
                ]
            )
        for index, entity in enumerate(entities):
            lines.extend(
                [
                    f"MERGE (e{index}:Entity {{name: {_quote(entity)}}})",
                    f"MERGE (m)-[:MENTIONS_ENTITY]->(e{index})",
                ]
            )
        for index, process in enumerate(processes):
            process_label = str(process["process_label"])
            lines.extend(
                [
                    f"MERGE (proc{index}:Process {{label: {_quote(process_label)}}})",
                    f"MERGE (m)-[:REFERS_TO_PROCESS]->(proc{index})",
                ]
            )
            if process.get("stage_ordinal") is not None:
                stage_key = f"{process_label}:{process['stage_ordinal']}"
                lines.extend(
                    [
                        f"MERGE (stage{index}:ProcessStage {{key: {_quote(stage_key)}}})",
                        f"SET stage{index}.ordinal = {int(process['stage_ordinal'])}",
                        f"MERGE (proc{index})-[:HAS_STAGE]->(stage{index})",
                        f"MERGE (m)-[:REFERS_TO_STAGE]->(stage{index})",
                    ]
                )
        for index, unit in enumerate(units):
            unit_uuid = str(unit["semantic_unit_uuid"])
            memory_kind = str(unit.get("memory_kind") or "MemoryClaim")
            node_label = memory_kind if memory_kind in _SAFE_UNIT_LABELS else "MemoryClaim"
            lines.extend(
                [
                    f"MERGE (u{index}:{node_label} {{uuid: {_quote(unit_uuid)}}})",
                    f"SET u{index}.epistemic_status = "
                    f"{_quote(str(unit.get('epistemic_status') or 'unknown'))}, "
                    f"u{index}.lifecycle_status = "
                    f"{_quote(str(unit.get('lifecycle_status') or 'unknown'))}",
                    f"MERGE (m)-[:DERIVED_CLAIM]->(u{index})",
                    f"MERGE (u{index})-[:EVIDENCES]->(m)",
                ]
            )
        return "\n".join(lines)

    def delete_erased_source(self, source_uuid: str) -> None:
        self.client.graph_query(
            f"MATCH (n {{uuid: {_quote(source_uuid)}}}) SET n.status = 'erased', n.text = null"
        )

    def jakobson_annotation_query(self, annotation: dict[str, Any]) -> str:
        annotation_uuid = str(annotation["annotation_uuid"])
        message_uuid = str(annotation["message_uuid"])
        unit_uuid = str(annotation["unit_uuid"])
        dominant_function = str(annotation["dominant_function"])
        receiver = str(annotation.get("receiver_value") or "unknown")
        context = str(annotation.get("context_value") or "unknown")
        code = str(annotation.get("code_value") or "unknown")
        return "\n".join(
            [
                f"MERGE (m:Message {{uuid: {_quote(message_uuid)}}})",
                f"MERGE (u:TextUnit {{uuid: {_quote(unit_uuid)}}})",
                f"MERGE (a:JakobsonAnnotation {{uuid: {_quote(annotation_uuid)}}})",
                f"MERGE (f:CommunicativeFunction {{name: {_quote(dominant_function)}}})",
                f"MERGE (r:Addressee {{value: {_quote(receiver)}}})",
                f"MERGE (c:ContextReferent {{value: {_quote(context)}}})",
                f"MERGE (k:CodeRegister {{value: {_quote(code)}}})",
                "MERGE (m)-[:HAS_UNIT]->(u)",
                "MERGE (u)-[:HAS_JAKOBSON_ANNOTATION]->(a)",
                "MERGE (a)-[:HAS_DOMINANT_FUNCTION]->(f)",
                "MERGE (a)-[:ADDRESSES]->(r)",
                "MERGE (a)-[:REFERS_TO]->(c)",
                "MERGE (a)-[:USES_CODE]->(k)",
            ]
        )

    def full_memory_record_query(self, record: dict[str, Any]) -> str:
        workspace_uuid = str(record["workspace_uuid"])
        project_uuid = str(record["project_uuid"])
        session_uuid = str(record["session_uuid"])
        message_uuid = str(record["message_uuid"])
        unit_uuid = str(record["unit_uuid"])
        annotation_uuid = str(record["annotation_uuid"])
        route_uuid = str(record["route_uuid"])
        candidate_uuid = str(record["candidate_uuid"])
        memory_uuid = str(record["memory_uuid"])
        memory_version_uuid = str(record["memory_version_uuid"])
        dominant_function = str(record.get("dominant_function") or "unknown")
        receiver = str(record.get("receiver_value") or "unknown")
        context = str(record.get("context_value") or "unknown")
        code = str(record.get("code_value") or "unknown")
        memory_status = str(record.get("memory_status") or "active")
        version_status = str(record.get("version_status") or "current")
        value = str(record.get("memory_value") or "")
        return "\n".join(
            [
                f"MERGE (w:Workspace {{uuid: {_quote(workspace_uuid)}}})",
                f"MERGE (p:Project {{uuid: {_quote(project_uuid)}}})",
                f"MERGE (s:Session {{uuid: {_quote(session_uuid)}}})",
                f"MERGE (m:Message {{uuid: {_quote(message_uuid)}}})",
                f"MERGE (u:TextUnit {{uuid: {_quote(unit_uuid)}}})",
                f"MERGE (a:JakobsonAnnotation {{uuid: {_quote(annotation_uuid)}}})",
                f"MERGE (f:CommunicativeFunction {{name: {_quote(dominant_function)}}})",
                f"MERGE (r:Addressee {{value: {_quote(receiver)}}})",
                f"MERGE (ctx:ContextReferent {{value: {_quote(context)}}})",
                f"MERGE (k:CodeRegister {{value: {_quote(code)}}})",
                f"MERGE (route:MemorySignalRoute {{uuid: {_quote(route_uuid)}}})",
                f"MERGE (candidate:MemoryCandidate {{uuid: {_quote(candidate_uuid)}}})",
                f"MERGE (mem:Memory {{uuid: {_quote(memory_uuid)}}})",
                f"MERGE (ver:MemoryVersion {{uuid: {_quote(memory_version_uuid)}}})",
                f"SET mem.status = {_quote(memory_status)}",
                f"SET ver.status = {_quote(version_status)}, ver.text = {_quote(value)}",
                "MERGE (w)-[:IN_PROJECT]->(p)",
                "MERGE (p)-[:HAS_SESSION]->(s)",
                "MERGE (s)-[:HAS_MESSAGE]->(m)",
                "MERGE (m)-[:HAS_UNIT]->(u)",
                "MERGE (u)-[:HAS_JAKOBSON_ANNOTATION]->(a)",
                "MERGE (a)-[:HAS_DOMINANT_FUNCTION]->(f)",
                "MERGE (a)-[:ADDRESSES]->(r)",
                "MERGE (a)-[:REFERS_TO]->(ctx)",
                "MERGE (a)-[:USES_CODE]->(k)",
                "MERGE (a)-[:ROUTES_TO]->(route)",
                "MERGE (route)-[:DERIVED_FROM]->(candidate)",
                "MERGE (candidate)-[:EVIDENCED_BY]->(a)",
                "MERGE (mem)-[:HAS_VERSION]->(ver)",
                "MERGE (ver)-[:EVIDENCED_BY]->(candidate)",
            ]
        )


def _quote(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


_SAFE_UNIT_LABELS = {
    "Instruction",
    "Need",
    "Preference",
    "Decision",
    "ProjectArtifact",
    "ProjectState",
    "FactClaim",
    "Hypothesis",
    "Constraint",
    "Question",
    "LearningGoal",
    "Feedback",
    "Correction",
    "Retraction",
}


def _as_strings(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if item]
    return []
