from collections.abc import Iterator

from memcore.imports.adapters.base import JsonAdapterMixin, no_match, text_part
from memcore.imports.models import FormatProbe, ImportRecord, StagedArtifact


class ClaudeAdapter(JsonAdapterMixin):
    adapter_id = "claude"
    adapter_version = "1"

    def probe(self, artifacts: list[StagedArtifact]) -> FormatProbe:
        for artifact, payload in self._json_artifacts(artifacts):
            items = payload if isinstance(payload, list) else [payload]
            if any(isinstance(item, dict) and "chat_messages" in item for item in items):
                return FormatProbe(
                    adapter_id=self.adapter_id,
                    adapter_version=self.adapter_version,
                    confidence=0.82,
                    detected_format="claude_conversation_export",
                    evidence=[artifact.relative_path, "chat_messages field found"],
                    warnings=["Claude public export schema is not promised stable"],
                )
        return no_match(self.adapter_id, self.adapter_version)

    def parse(self, artifacts: list[StagedArtifact]) -> Iterator[ImportRecord]:
        for _artifact, payload in self._json_artifacts(artifacts):
            items = payload if isinstance(payload, list) else [payload]
            for index, conversation in enumerate(items):
                if isinstance(conversation, dict) and "chat_messages" in conversation:
                    yield ImportRecord(
                        source_record_type="conversation",
                        source_record_id=str(
                            conversation.get("uuid") or conversation.get("id") or index
                        ),
                        raw_payload=conversation,
                        normalized_payload=_normalize_claude(conversation),
                        reconstruction_confidence=0.75,
                    )


def _normalize_claude(conversation: dict[object, object]) -> dict[str, object]:
    messages = {}
    previous_id: str | None = None
    roots: list[str] = []
    raw_messages = conversation.get("chat_messages", [])
    message_items = raw_messages if isinstance(raw_messages, list) else []
    for index, message in enumerate(message_items):
        if not isinstance(message, dict):
            continue
        message_id = str(message.get("uuid") or message.get("id") or index)
        if previous_id is None:
            roots.append(message_id)
        messages[message_id] = {
            "source_message_id": message_id,
            "parent_ids": [previous_id] if previous_id else [],
            "child_ids": [],
            "role": message.get("sender") or message.get("role") or "unknown",
            "creator_label": message.get("sender"),
            "content_parts": [text_part(str(message.get("text") or message.get("content") or ""))],
            "created_at": message.get("created_at"),
            "timestamp_confidence": "provider" if message.get("created_at") else "missing",
            "model_name": message.get("model"),
            "branch_status": "active",
            "metadata": {"provider": {"claude": message}},
        }
        if previous_id and previous_id in messages:
            messages[previous_id]["child_ids"] = [message_id]
        previous_id = message_id
    return {
        "source_platform": "claude",
        "source_conversation_id": conversation.get("uuid") or conversation.get("id"),
        "title": conversation.get("name") or conversation.get("title"),
        "created_at": conversation.get("created_at"),
        "updated_at": conversation.get("updated_at"),
        "roots": roots,
        "active_leaf_id": previous_id,
        "messages": messages,
        "metadata": {"provider": {"claude": conversation}},
        "reconstruction_issues": [
            {
                "severity": "warning",
                "issue_code": "branching_unavailable",
                "message": "Claude branching information was unavailable in this export shape",
            }
        ],
    }
