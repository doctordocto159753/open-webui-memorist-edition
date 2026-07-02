from collections.abc import Iterator

from memcore.imports.adapters.base import JsonAdapterMixin, no_match, text_part
from memcore.imports.models import FormatProbe, ImportRecord, StagedArtifact


class OpenWebUIAdapter(JsonAdapterMixin):
    adapter_id = "openwebui"
    adapter_version = "1"

    def probe(self, artifacts: list[StagedArtifact]) -> FormatProbe:
        for artifact, payload in self._json_artifacts(artifacts):
            chats = payload if isinstance(payload, list) else [payload]
            for item in chats:
                if isinstance(item, dict) and _has_openwebui_history(item):
                    return FormatProbe(
                        adapter_id=self.adapter_id,
                        adapter_version=self.adapter_version,
                        confidence=0.95,
                        detected_format="openwebui_message_tree",
                        detected_format_version="documented",
                        evidence=[artifact.relative_path, "chat.history.messages found"],
                    )
        return no_match(self.adapter_id, self.adapter_version)

    def parse(self, artifacts: list[StagedArtifact]) -> Iterator[ImportRecord]:
        for _artifact, payload in self._json_artifacts(artifacts):
            chats = payload if isinstance(payload, list) else [payload]
            for index, chat in enumerate(chats):
                if isinstance(chat, dict) and _has_openwebui_history(chat):
                    yield ImportRecord(
                        source_record_type="conversation",
                        source_record_id=str(chat.get("id") or index),
                        raw_payload=chat,
                        normalized_payload=_normalize_openwebui(chat),
                    )


def _has_openwebui_history(item: dict[object, object]) -> bool:
    chat = item.get("chat")
    if not isinstance(chat, dict):
        return False
    history = chat.get("history")
    return isinstance(history, dict) and isinstance(history.get("messages"), dict)


def _normalize_openwebui(chat: dict[object, object]) -> dict[str, object]:
    history = chat["chat"]["history"]  # type: ignore[index]
    messages = history.get("messages", {})
    normalized_messages = {}
    roots: list[str] = []
    for message_id, message in messages.items():
        if not isinstance(message, dict):
            continue
        parent_id = message.get("parentId")
        if not parent_id:
            roots.append(str(message_id))
        normalized_messages[str(message_id)] = {
            "source_message_id": str(message_id),
            "parent_ids": [str(parent_id)] if parent_id else [],
            "child_ids": [str(child) for child in message.get("childrenIds", [])],
            "role": message.get("role") or "unknown",
            "creator_label": message.get("user"),
            "content_parts": [text_part(str(message.get("content") or ""))],
            "created_at": message.get("timestamp"),
            "timestamp_confidence": "provider",
            "model_name": message.get("model"),
            "branch_status": "active"
            if str(message_id) == str(history.get("currentId"))
            else "branch",
            "metadata": {"provider": {"openwebui": message}},
        }
    return {
        "source_platform": "openwebui",
        "source_conversation_id": chat.get("id"),
        "title": chat.get("title"),
        "created_at": chat.get("created_at"),
        "updated_at": chat.get("updated_at"),
        "roots": roots,
        "active_leaf_id": history.get("currentId"),
        "messages": normalized_messages,
        "metadata": {"provider": {"openwebui": chat}},
        "reconstruction_issues": [],
    }
