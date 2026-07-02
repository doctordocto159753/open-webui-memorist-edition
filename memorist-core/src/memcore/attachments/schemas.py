from typing import Any

from pydantic import BaseModel


class AttachmentAbstention(BaseModel):
    status: str = "none"
    reason: str | None = None


class AttachmentSecurity(BaseModel):
    content_trust: str = "mixed"
    instruction_like_content_detected: bool = False


class AttachmentPacket(BaseModel):
    attachment_version: str = "1.0"
    attachment_uuid: str
    retrieval_run_uuid: str
    session_uuid: str
    input_message_uuid: str
    mode: str
    generated_at: str
    trusted_directives: list[dict[str, Any]]
    current_context: list[dict[str, Any]]
    relevant_memories: list[dict[str, Any]]
    historical_context: list[dict[str, Any]]
    conflicts_and_uncertainty: list[dict[str, Any]]
    abstention: AttachmentAbstention
    provenance: list[dict[str, Any]]
    security: AttachmentSecurity
