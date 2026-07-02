from collections.abc import Iterator
from pathlib import Path
from typing import Any

from memcore.imports.adapters.base import JsonAdapterMixin, no_match
from memcore.imports.models import FormatProbe, ImportRecord, StagedArtifact


class GenericMemoristAdapter(JsonAdapterMixin):
    adapter_id = "generic_memorist"
    adapter_version = "1"

    def probe(self, artifacts: list[StagedArtifact]) -> FormatProbe:
        for artifact, payload in self._json_artifacts(artifacts):
            if (
                isinstance(payload, dict)
                and payload.get("format") == "memorist-conversation-import"
            ):
                return FormatProbe(
                    adapter_id=self.adapter_id,
                    adapter_version=self.adapter_version,
                    confidence=0.99,
                    detected_format="memorist-conversation-import",
                    detected_format_version=str(payload.get("version") or "unknown"),
                    evidence=[artifact.relative_path, "format field matched"],
                )
        return no_match(self.adapter_id, self.adapter_version)

    def parse(self, artifacts: list[StagedArtifact]) -> Iterator[ImportRecord]:
        for _artifact, payload in self._json_artifacts(artifacts):
            if not isinstance(payload, dict):
                continue
            conversations = payload.get("conversations")
            if not isinstance(conversations, list):
                continue
            for index, conversation in enumerate(conversations):
                if isinstance(conversation, dict):
                    yield ImportRecord(
                        source_record_type="conversation",
                        source_record_id=_source_id(conversation, index),
                        raw_payload=conversation,
                        normalized_payload=_normalize_conversation(conversation, "generic"),
                    )


def _source_id(conversation: dict[str, Any], index: int) -> str:
    return str(conversation.get("id") or conversation.get("source_conversation_id") or index)


def _normalize_conversation(conversation: dict[str, Any], platform: str) -> dict[str, Any]:
    messages = (
        conversation.get("messages") if isinstance(conversation.get("messages"), dict) else {}
    )
    return {
        "source_platform": platform,
        "source_conversation_id": conversation.get("id")
        or conversation.get("source_conversation_id"),
        "title": conversation.get("title"),
        "created_at": conversation.get("created_at"),
        "updated_at": conversation.get("updated_at"),
        "roots": conversation.get("roots") or [],
        "active_leaf_id": conversation.get("active_leaf_id"),
        "messages": messages,
        "metadata": {"provider": {platform: conversation}},
        "reconstruction_issues": [],
    }


class ManualTranscriptAdapter(JsonAdapterMixin):
    adapter_id = "manual_transcript"
    adapter_version = "1"

    def probe(self, artifacts: list[StagedArtifact]) -> FormatProbe:
        text_files = [
            artifact.relative_path
            for artifact in artifacts
            if artifact.relative_path.lower().endswith((".md", ".txt"))
        ]
        if not text_files:
            return no_match(self.adapter_id, self.adapter_version)
        return FormatProbe(
            adapter_id=self.adapter_id,
            adapter_version=self.adapter_version,
            confidence=0.3,
            detected_format="manual_transcript",
            evidence=text_files[:3],
            warnings=[
                "manual transcript requires explicit user selection because roles may be ambiguous"
            ],
        )

    def parse(self, artifacts: list[StagedArtifact]) -> Iterator[ImportRecord]:
        for artifact in artifacts:
            if not artifact.object_store_path.lower().endswith((".md", ".txt")):
                continue
            text = Path(artifact.object_store_path).read_text(encoding="utf-8")
            yield ImportRecord(
                source_record_type="manual_transcript",
                source_record_id=artifact.relative_path,
                raw_payload={"text": text},
                normalized_payload={
                    "source_platform": "manual",
                    "source_conversation_id": artifact.relative_path,
                    "title": artifact.relative_path,
                    "roots": [],
                    "active_leaf_id": None,
                    "messages": {},
                    "metadata": {"provider": {"manual": {"line_count": len(text.splitlines())}}},
                    "reconstruction_issues": [
                        {
                            "severity": "warning",
                            "issue_code": "ambiguous_roles",
                            "message": (
                                "Manual transcript needs span-grounded reconstruction before commit"
                            ),
                        }
                    ],
                },
                reconstruction_confidence=0.2,
            )
