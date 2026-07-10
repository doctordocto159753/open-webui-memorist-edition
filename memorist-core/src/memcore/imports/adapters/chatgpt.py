from collections.abc import Iterator
from copy import deepcopy
from typing import Any

from memcore.imports.adapters.base import JsonAdapterMixin, no_match, text_part
from memcore.imports.models import FormatProbe, ImportRecord, StagedArtifact
from memcore.imports.reconstruction.timestamps import normalize_timestamp


class ChatGPTAdapter(JsonAdapterMixin):
    adapter_id = "chatgpt"
    adapter_version = "2"

    def probe(self, artifacts: list[StagedArtifact]) -> FormatProbe:
        for artifact, payload in self._json_artifacts(artifacts):
            conversations = payload if isinstance(payload, list) else [payload]
            if any(isinstance(item, dict) and "mapping" in item for item in conversations):
                return FormatProbe(
                    adapter_id=self.adapter_id,
                    adapter_version=self.adapter_version,
                    confidence=0.98,
                    detected_format="chatgpt_conversation_mapping",
                    evidence=[
                        artifact.relative_path,
                        "top-level conversation list",
                        "mapping tree field found",
                    ],
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
    if not isinstance(mapping, dict):
        mapping = {}
    messages = {}
    roots: list[str] = []
    active_path = _active_path(mapping, conversation.get("current_node"))
    for node_id, node in mapping.items():
        if not isinstance(node, dict):
            continue
        source_message = node.get("message")
        message = source_message if isinstance(source_message, dict) else {}
        author = message.get("author") if isinstance(message, dict) else {}
        content = message.get("content") if isinstance(message, dict) else {}
        parts = content.get("parts") if isinstance(content, dict) else []
        content_type = str(content.get("content_type") or "") if isinstance(content, dict) else ""
        metadata_value = message.get("metadata")
        metadata: dict[str, Any] = metadata_value if isinstance(metadata_value, dict) else {}
        parent = node.get("parent")
        if not parent:
            roots.append(str(node_id))
        messages[str(node_id)] = {
            "source_message_id": str(node_id),
            "parent_ids": [str(parent)] if parent else [],
            "child_ids": [str(child) for child in node.get("children", [])],
            "role": (author or {}).get("role") or "unknown",
            "creator_label": (author or {}).get("name"),
            "content_parts": (
                [_part(part, content_type=content_type) for part in parts]
                if isinstance(parts, list)
                else []
            ),
            "created_at": normalize_timestamp(message.get("create_time")),
            "timestamp_confidence": "provider" if message.get("create_time") else "missing",
            "model_name": metadata.get("model_slug") or conversation.get("default_model_slug"),
            "branch_status": "active" if str(node_id) in active_path else "branch",
            "metadata": {
                "provider": {"chatgpt": _provider_snapshot(node)},
                "source_node_has_message": isinstance(source_message, dict),
                "source_update_time": message.get("update_time"),
                "model_slug": metadata.get("model_slug"),
                "default_model_slug": conversation.get("default_model_slug"),
                "attachments": metadata.get("attachments", []),
            },
        }
    return {
        "source_platform": "chatgpt",
        "source_conversation_id": conversation.get("id"),
        "title": conversation.get("title"),
        "created_at": normalize_timestamp(conversation.get("create_time")),
        "updated_at": normalize_timestamp(conversation.get("update_time")),
        "roots": roots,
        "active_leaf_id": conversation.get("current_node"),
        "messages": messages,
        "metadata": {"provider": {"chatgpt": conversation}},
        "reconstruction_issues": [],
    }


def _part(part: object, content_type: str = "") -> dict[str, object]:
    if _is_internal_reasoning_type(content_type):
        return {"part_type": "provider_internal_reasoning", "quarantined": True}
    if isinstance(part, str):
        return text_part(part)
    if isinstance(part, dict):
        part_type = str(part.get("content_type") or part.get("type") or "unknown")
        if _is_internal_reasoning_type(part_type):
            return {"part_type": "provider_internal_reasoning", "quarantined": True}
        return {"part_type": part_type, "raw": part}
    return {"part_type": "unknown", "raw": str(part)}


def _active_path(mapping: dict[str, Any], current_node: object) -> set[str]:
    active: set[str] = set()
    cursor = str(current_node) if current_node is not None else None
    while cursor and cursor not in active:
        active.add(cursor)
        node = mapping.get(cursor)
        parent = node.get("parent") if isinstance(node, dict) else None
        cursor = str(parent) if parent else None
    return active


def _is_internal_reasoning_type(value: str) -> bool:
    lowered = value.lower()
    return any(marker in lowered for marker in ("reasoning", "thought", "scratchpad"))


def _provider_snapshot(node: dict[str, Any]) -> dict[str, Any]:
    """Preserve provider metadata while removing internal reasoning payload text."""
    snapshot = deepcopy(node)
    message = snapshot.get("message")
    if not isinstance(message, dict):
        return snapshot
    content = message.get("content")
    if not isinstance(content, dict):
        return snapshot
    content_type = str(content.get("content_type") or "")
    parts = content.get("parts")
    if _is_internal_reasoning_type(content_type):
        content["parts"] = [{"quarantined": True}]
    elif isinstance(parts, list):
        content["parts"] = [
            {"quarantined": True}
            if isinstance(part, dict)
            and _is_internal_reasoning_type(
                str(part.get("content_type") or part.get("type") or "")
            )
            else part
            for part in parts
        ]
    return snapshot
