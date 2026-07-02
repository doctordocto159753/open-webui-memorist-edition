from typing import Any

from memcore.imports.models import ImportIssue
from memcore.imports.reconstruction.content_parts import normalize_content_part
from memcore.imports.reconstruction.models import ImportedConversation, ImportedMessageNode
from memcore.imports.reconstruction.roles import normalize_role
from memcore.imports.reconstruction.timestamps import normalize_timestamp
from memcore.imports.reconstruction.validation import validate_conversation


def normalize_conversation(payload: dict[str, Any]) -> ImportedConversation:
    messages_payload = payload.get("messages", {})
    messages: dict[str, ImportedMessageNode] = {}
    if isinstance(messages_payload, dict):
        for message_id, message in messages_payload.items():
            if not isinstance(message, dict):
                continue
            parts = message.get("content_parts", [])
            if not isinstance(parts, list):
                parts = []
            metadata = message.get("metadata")
            messages[str(message_id)] = ImportedMessageNode(
                source_message_id=message.get("source_message_id") or str(message_id),
                parent_ids=[str(parent) for parent in message.get("parent_ids", [])],
                child_ids=[str(child) for child in message.get("child_ids", [])],
                role=normalize_role(message.get("role")),
                creator_label=message.get("creator_label"),
                content_parts=[normalize_content_part(part) for part in parts],
                created_at=normalize_timestamp(message.get("created_at")),
                timestamp_confidence=message.get("timestamp_confidence") or "missing",
                model_name=message.get("model_name"),
                branch_status=message.get("branch_status") or "active",
                metadata=metadata if isinstance(metadata, dict) else {},
            )
    issues = [
        ImportIssue.model_validate(issue)
        for issue in payload.get("reconstruction_issues", [])
        if isinstance(issue, dict)
    ]
    metadata = payload.get("metadata")
    conversation = ImportedConversation(
        source_platform=str(payload.get("source_platform") or "unknown"),
        source_conversation_id=payload.get("source_conversation_id"),
        title=payload.get("title"),
        created_at=normalize_timestamp(payload.get("created_at")),
        updated_at=normalize_timestamp(payload.get("updated_at")),
        roots=[str(root) for root in payload.get("roots", [])],
        active_leaf_id=str(payload["active_leaf_id"]) if payload.get("active_leaf_id") else None,
        messages=messages,
        metadata=metadata if isinstance(metadata, dict) else {},
        reconstruction_issues=issues,
    )
    conversation.reconstruction_issues.extend(validate_conversation(conversation))
    return conversation
