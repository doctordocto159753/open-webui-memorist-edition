from collections.abc import Iterator
from typing import Any

from memcore.imports.adapters.base import JsonAdapterMixin, no_match, text_part
from memcore.imports.models import FormatProbe, ImportRecord, StagedArtifact


class ChatGPTAdapter(JsonAdapterMixin):
    adapter_id = "chatgpt"
    adapter_version = "1"

    def probe(self, artifacts: list[StagedArtifact]) -> FormatProbe:
        for artifact, payload in self._json_artifacts(artifacts):
            conversations = payload if isinstance(payload, list) else [payload]
            if any(isinstance(item, dict) and "mapping" in item for item in conversations):
                return FormatProbe(
                    adapter_id=self.adapter_id,
                    adapter_version=self.adapter_version,
                    confidence=0.9,
                    detected_format="chatgpt_conversation_mapping",
                    evidence=[artifact.relative_path, "mapping field found"],
                )
        return no_match(self.adapter_id, self.adapter_version)

    def parse(self, artifacts: list[StagedArtifact]) -> Iterator[ImportRecord]:
        for _artifact, payload in self._json_artifacts(artifacts):
            conversations = payload if isinstance(payload, list) else [payload]
            for index, conversation in enumerate(conversations):
                if isinstance(conversation, dict) and "mapping" in conversation:
                    yield ImportRecord(
                        source_record_type="conversation",
                        source_record_id=str(conversation.get("id") or index),
                        raw_payload=conversation,
                        normalized_payload=_normalize_chatgpt(conversation),
                    )


def _normalize_chatgpt(conversation: dict[str, Any]) -> dict[str, object]:
    mapping = conversation.get("mapping", {})
    messages = {}
    roots: list[str] = []
    for node_id, node in mapping.items():
        if not isinstance(node, dict):
            continue
        message = node.get("message") or {}
        author = message.get("author") if isinstance(message, dict) else {}
        content = message.get("content") if isinstance(message, dict) else {}
        parts = content.get("parts") if isinstance(content, dict) else []
        parent = node.get("parent")
        if not parent:
            roots.append(str(node_id))
        messages[str(node_id)] = {
            "source_message_id": str(node_id),
            "parent_ids": [str(parent)] if parent else [],
            "child_ids": [str(child) for child in node.get("children", [])],
            "role": (author or {}).get("role") or "unknown",
            "creator_label": (author or {}).get("name"),
            "content_parts": [_part(part) for part in parts] if isinstance(parts, list) else [],
            "created_at": message.get("create_time"),
            "timestamp_confidence": "provider" if message.get("create_time") else "missing",
            "model_name": (message.get("metadata") or {}).get("model_slug"),
            "branch_status": "active"
            if str(node_id) == str(conversation.get("current_node"))
            else "branch",
            "metadata": {"provider": {"chatgpt": node}},
        }
    return {
        "source_platform": "chatgpt",
        "source_conversation_id": conversation.get("id"),
        "title": conversation.get("title"),
        "created_at": conversation.get("create_time"),
        "updated_at": conversation.get("update_time"),
        "roots": roots,
        "active_leaf_id": conversation.get("current_node"),
        "messages": messages,
        "metadata": {"provider": {"chatgpt": conversation}},
        "reconstruction_issues": [],
    }


def _part(part: object) -> dict[str, object]:
    if isinstance(part, str):
        return text_part(part)
    if isinstance(part, dict):
        part_type = str(part.get("content_type") or part.get("type") or "unknown")
        if "reasoning" in part_type.lower():
            return {"part_type": "provider_internal_reasoning", "quarantined": True}
        return {"part_type": part_type, "raw": part}
    return {"part_type": "unknown", "raw": str(part)}
