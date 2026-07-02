import sqlite3
from typing import Any

from memcore.governance.repositories import GovernanceRepository
from memcore.validators.ijson import load_ijson


class DeliveryTraceService:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self.repository = GovernanceRepository(connection)

    def log_retrieval_run_delivery(self, retrieval_run_uuid: str, input_message_uuid: str) -> None:
        rows = self.connection.execute(
            """
            SELECT *
            FROM retrieval_candidates
            WHERE retrieval_run_uuid = ?
            ORDER BY COALESCE(final_score, fused_score, 0) DESC, created_at
            """,
            (retrieval_run_uuid,),
        ).fetchall()
        for index, row in enumerate(rows, start=1):
            stage = "selected" if row["decision"] == "selected" else "excluded"
            score = row["final_score"] if row["final_score"] is not None else row["fused_score"]
            self.repository.record_delivery_event(
                input_message_uuid=input_message_uuid,
                retrieval_run_uuid=retrieval_run_uuid,
                memory_uuid=row["memory_uuid"],
                memory_version_uuid=row["memory_version_uuid"],
                delivery_stage="retrieved",
                rank_at_stage=index,
                score_at_stage=score,
            )
            self.repository.record_delivery_event(
                input_message_uuid=input_message_uuid,
                retrieval_run_uuid=retrieval_run_uuid,
                memory_uuid=row["memory_uuid"],
                memory_version_uuid=row["memory_version_uuid"],
                delivery_stage=stage,
                rank_at_stage=index,
                score_at_stage=score,
            )

    def log_attachment_rendered(self, attachment_uuid: str) -> None:
        row = self.connection.execute(
            "SELECT * FROM memory_context_attachments WHERE attachment_uuid = ?",
            (attachment_uuid,),
        ).fetchone()
        if row is None:
            return
        for index, item in enumerate(_attachment_provenance(row["ijson_attachment"]), start=1):
            self.repository.record_delivery_event(
                input_message_uuid=row["input_message_uuid"],
                attachment_uuid=attachment_uuid,
                retrieval_run_uuid=row["retrieval_run_uuid"],
                memory_uuid=str(item["memory_uuid"]),
                memory_version_uuid=str(item["memory_version_uuid"]),
                delivery_stage="rendered",
                rank_at_stage=index,
            )

    def log_attachment_injected(self, attachment_uuid: str) -> None:
        row = self.connection.execute(
            "SELECT * FROM memory_context_attachments WHERE attachment_uuid = ?",
            (attachment_uuid,),
        ).fetchone()
        if row is None:
            return
        for index, item in enumerate(_attachment_provenance(row["ijson_attachment"]), start=1):
            self.repository.record_delivery_event(
                input_message_uuid=row["input_message_uuid"],
                attachment_uuid=attachment_uuid,
                retrieval_run_uuid=row["retrieval_run_uuid"],
                memory_uuid=str(item["memory_uuid"]),
                memory_version_uuid=str(item["memory_version_uuid"]),
                delivery_stage="injected",
                rank_at_stage=index,
            )

    def attribute_response(
        self,
        response_message_uuid: str,
        attachment_uuid: str | None,
        assistant_text: str,
    ) -> None:
        if attachment_uuid is None:
            return
        row = self.connection.execute(
            "SELECT * FROM memory_context_attachments WHERE attachment_uuid = ?",
            (attachment_uuid,),
        ).fetchone()
        if row is None:
            return
        packet = load_ijson(row["ijson_attachment"])
        items = _attachment_items(packet)
        for item in items:
            memory_uuid = str(item["memory_uuid"])
            version_uuid = str(item["memory_version_uuid"])
            text = str(item.get("normalized_text", ""))
            span = _find_overlap_span(assistant_text, text)
            if span is None:
                self.repository.record_attribution(
                    response_message_uuid,
                    memory_uuid,
                    version_uuid,
                    attribution_type="memory_context",
                    attribution_status="unused",
                    method="deterministic_overlap",
                    confidence=0.2,
                    evidence={"reason": "no deterministic overlap"},
                )
                continue
            start, end = span
            self.repository.record_attribution(
                response_message_uuid,
                memory_uuid,
                version_uuid,
                attribution_type="memory_context",
                attribution_status="supported",
                method="deterministic_overlap",
                response_span_text=assistant_text[start:end],
                response_start_char=start,
                response_end_char=end,
                confidence=0.7,
                evidence={"matched_memory_text": text[:120]},
            )

    def response_trace(self, response_message_uuid: str) -> dict[str, Any]:
        attributions = [
            dict(row)
            for row in self.connection.execute(
                """
                SELECT *
                FROM response_memory_attributions
                WHERE response_message_uuid = ?
                ORDER BY created_at, attribution_uuid
                """,
                (response_message_uuid,),
            )
        ]
        delivery = [
            dict(row)
            for row in self.connection.execute(
                """
                SELECT mde.*
                FROM memory_delivery_events mde
                WHERE mde.response_message_uuid = ?
                   OR mde.attachment_uuid IN (
                        SELECT attachment_uuid
                        FROM assistant_response_links
                        WHERE assistant_message_uuid = ?
                   )
                ORDER BY created_at, delivery_event_uuid
                """,
                (response_message_uuid, response_message_uuid),
            )
        ]
        return {
            "response_message_uuid": response_message_uuid,
            "delivery_events": delivery,
            "attributions": attributions,
        }

    def attachment_delivery(self, attachment_uuid: str) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in self.connection.execute(
                """
                SELECT *
                FROM memory_delivery_events
                WHERE attachment_uuid = ?
                ORDER BY created_at, delivery_event_uuid
                """,
                (attachment_uuid,),
            )
        ]

    def memory_usage(self, memory_uuid: str) -> dict[str, Any]:
        delivery = [
            dict(row)
            for row in self.connection.execute(
                "SELECT * FROM memory_delivery_events WHERE memory_uuid = ?",
                (memory_uuid,),
            )
        ]
        attributions = [
            dict(row)
            for row in self.connection.execute(
                "SELECT * FROM response_memory_attributions WHERE memory_uuid = ?",
                (memory_uuid,),
            )
        ]
        feedback = [
            dict(row)
            for row in self.connection.execute(
                "SELECT * FROM memory_feedback WHERE memory_uuid = ?",
                (memory_uuid,),
            )
        ]
        return {
            "memory_uuid": memory_uuid,
            "delivery_events": delivery,
            "attributions": attributions,
            "feedback": feedback,
        }


def _attachment_provenance(ijson_attachment: str) -> list[dict[str, object]]:
    packet = load_ijson(ijson_attachment)
    provenance = packet.get("provenance", []) if isinstance(packet, dict) else []
    return [item for item in provenance if isinstance(item, dict)]


def _attachment_items(packet: object) -> list[dict[str, object]]:
    if not isinstance(packet, dict):
        return []
    items: list[dict[str, object]] = []
    for key in (
        "trusted_directives",
        "current_context",
        "relevant_memories",
        "historical_context",
        "conflicts_and_uncertainty",
    ):
        value = packet.get(key)
        if isinstance(value, list):
            items.extend(item for item in value if isinstance(item, dict))
    return items


def _find_overlap_span(response: str, memory_text: str) -> tuple[int, int] | None:
    terms = [term.strip(" .,:;").lower() for term in memory_text.split() if len(term) > 4]
    for term in terms:
        index = response.lower().find(term)
        if index >= 0:
            return index, index + len(term)
    return None
