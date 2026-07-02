import sqlite3
from typing import Any

from memcore.attachments.budget import budget_for_mode
from memcore.attachments.renderer import render_attachment
from memcore.attachments.schemas import AttachmentAbstention, AttachmentPacket, AttachmentSecurity
from memcore.attachments.security import instruction_like_content_detected
from memcore.governance.delivery import DeliveryTraceService
from memcore.models import ScoredMemoryItem, new_uuid, utc_now
from memcore.repositories import MemoryContextAttachmentRepository


class AttachmentBuilder:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def build(
        self,
        retrieval_run_uuid: str,
        session_uuid: str,
        input_message_uuid: str,
        mode: str,
        selected_items: list[ScoredMemoryItem],
        token_budget: int,
        abstention_status: str | None = None,
        abstention_reason: str | None = None,
    ) -> tuple[str, str, int]:
        attachment_uuid = new_uuid()
        budget = budget_for_mode(mode, token_budget)
        packet = self._packet(
            attachment_uuid,
            retrieval_run_uuid,
            session_uuid,
            input_message_uuid,
            mode,
            selected_items,
            abstention_status,
            abstention_reason,
        )
        rendered, token_count = render_attachment(packet, budget)
        repository = MemoryContextAttachmentRepository(self.connection)
        source_memory_uuids = [item.memory_uuid for item in selected_items]
        source_block_uuids: list[str] = []
        source_fact_edge_uuids: list[str] = []
        attachment = repository.create_attachment(
            session_uuid=session_uuid,
            attachment_mode=mode,
            ijson_attachment=packet.model_dump(mode="json"),
            attachment_uuid=attachment_uuid,
            input_message_uuid=input_message_uuid,
            retrieval_run_uuid=retrieval_run_uuid,
            rendered_attachment=rendered,
            source_memory_uuids=source_memory_uuids,
            source_block_uuids=source_block_uuids,
            source_fact_edge_uuids=source_fact_edge_uuids,
            token_count=token_count,
            confidence=0.5 if abstention_status else 0.8,
            abstention_status=abstention_status,
        )
        DeliveryTraceService(self.connection).log_attachment_rendered(attachment.attachment_uuid)
        return attachment.attachment_uuid, rendered, token_count

    def _packet(
        self,
        attachment_uuid: str,
        retrieval_run_uuid: str,
        session_uuid: str,
        input_message_uuid: str,
        mode: str,
        selected_items: list[ScoredMemoryItem],
        abstention_status: str | None,
        abstention_reason: str | None,
    ) -> AttachmentPacket:
        trusted_directives = [
            _item_to_packet(item)
            for item in selected_items
            if item.memory_type == "constraint" and item.scope_type in {"project", "workspace"}
        ]
        untrusted_items = [_item_to_packet(item) for item in selected_items]
        instruction_detected = any(
            instruction_like_content_detected(item.normalized_text)
            or instruction_like_content_detected(item.evidence_text or "")
            for item in selected_items
        )
        return AttachmentPacket(
            attachment_uuid=attachment_uuid,
            retrieval_run_uuid=retrieval_run_uuid,
            session_uuid=session_uuid,
            input_message_uuid=input_message_uuid,
            mode=mode,
            generated_at=utc_now(),
            trusted_directives=trusted_directives,
            current_context=[item for item in untrusted_items if item["current"] is True],
            relevant_memories=[
                item for item in untrusted_items if item["memory_type"] not in {"constraint"}
            ],
            historical_context=[item for item in untrusted_items if item["current"] is False],
            conflicts_and_uncertainty=[
                item for item in untrusted_items if item["conflict_status"] != "none"
            ],
            abstention=AttachmentAbstention(
                status=abstention_status or "none",
                reason=abstention_reason,
            ),
            provenance=[
                {
                    "memory_uuid": item.memory_uuid,
                    "memory_version_uuid": item.memory_version_uuid,
                }
                for item in selected_items
            ],
            security=AttachmentSecurity(
                content_trust="mixed",
                instruction_like_content_detected=instruction_detected,
            ),
        )


def _item_to_packet(item: ScoredMemoryItem) -> dict[str, Any]:
    return {
        "memory_uuid": item.memory_uuid,
        "memory_version_uuid": item.memory_version_uuid,
        "memory_type": item.memory_type,
        "scope": {"scope_type": item.scope_type, "scope_uuid": item.scope_uuid},
        "normalized_text": item.normalized_text,
        "valid_time_label": item.valid_time_label,
        "current": item.current,
        "confidence_label": item.confidence_label,
        "source_authority_label": item.source_authority_label,
        "evidence_text": item.evidence_text,
        "conflict_status": item.conflict_status,
    }
