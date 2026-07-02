from collections.abc import Iterator
from html.parser import HTMLParser
from pathlib import Path

from memcore.imports.adapters.base import JsonAdapterMixin, no_match, text_part
from memcore.imports.models import FormatProbe, ImportRecord, StagedArtifact


class GeminiAdapter(JsonAdapterMixin):
    adapter_id = "gemini"
    adapter_version = "1"

    def probe(self, artifacts: list[StagedArtifact]) -> FormatProbe:
        evidence = [
            artifact.relative_path
            for artifact in artifacts
            if "gemini" in artifact.relative_path.lower()
            or artifact.artifact_role == "html_activity"
        ]
        for artifact, payload in self._json_artifacts(artifacts):
            rows = payload if isinstance(payload, list) else [payload]
            if any(_looks_like_gemini_row(row) for row in rows):
                evidence.append(artifact.relative_path)
        if evidence:
            return FormatProbe(
                adapter_id=self.adapter_id,
                adapter_version=self.adapter_version,
                confidence=0.7,
                detected_format="google_takeout_gemini_activity",
                evidence=evidence[:3],
                warnings=["Google Takeout archives may have snapshot gaps"],
            )
        return no_match(self.adapter_id, self.adapter_version)

    def parse(self, artifacts: list[StagedArtifact]) -> Iterator[ImportRecord]:
        for artifact, payload in self._json_artifacts(artifacts):
            rows = payload if isinstance(payload, list) else [payload]
            for index, row in enumerate(rows):
                if isinstance(row, dict):
                    yield ImportRecord(
                        source_record_type="activity",
                        source_record_id=str(row.get("id") or row.get("time") or index),
                        raw_payload=row,
                        normalized_payload=_normalize_gemini_json(row, artifact.relative_path),
                        reconstruction_confidence=0.55,
                    )
        for artifact in artifacts:
            if artifact.artifact_role != "html_activity":
                continue
            text = _HTMLTextExtractor.extract(
                Path(artifact.object_store_path).read_text(encoding="utf-8")
            )
            yield ImportRecord(
                source_record_type="html_activity",
                source_record_id=artifact.relative_path,
                raw_payload={"html_text": text},
                normalized_payload=_normalize_gemini_html(text, artifact.relative_path),
                reconstruction_confidence=0.35,
            )


def _normalize_gemini_json(row: dict[object, object], source: str) -> dict[str, object]:
    text = str(row.get("title") or row.get("text") or row.get("query") or "")
    message_id = str(row.get("id") or row.get("time") or source)
    return {
        "source_platform": "gemini",
        "source_conversation_id": row.get("conversation_id") or source,
        "title": row.get("title"),
        "created_at": row.get("time") or row.get("timestamp"),
        "updated_at": None,
        "roots": [message_id],
        "active_leaf_id": message_id,
        "messages": {
            message_id: {
                "source_message_id": message_id,
                "parent_ids": [],
                "child_ids": [],
                "role": "user",
                "creator_label": "gemini_activity",
                "content_parts": [text_part(text)],
                "created_at": row.get("time") or row.get("timestamp"),
                "timestamp_confidence": "provider",
                "model_name": None,
                "branch_status": "active",
                "metadata": {"provider": {"gemini": row}},
            }
        },
        "metadata": {"provider": {"gemini": row}, "possible_snapshot_gap": True},
        "reconstruction_issues": [
            {
                "severity": "warning",
                "issue_code": "possibly_incomplete_activity_record",
                "message": "Gemini activity may not contain generated response text",
            }
        ],
    }


def _looks_like_gemini_row(row: object) -> bool:
    if not isinstance(row, dict):
        return False
    text = " ".join(
        str(row.get(key, "")) for key in ("title", "source", "product", "service", "text")
    ).lower()
    if "gemini" in text:
        return True
    return "time" in row and any(key in row for key in ("title", "text", "query"))


def _normalize_gemini_html(text: str, source: str) -> dict[str, object]:
    return _normalize_gemini_json({"id": source, "title": source, "text": text}, source)


class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        if data.strip():
            self.parts.append(data.strip())

    @classmethod
    def extract(cls, html_text: str) -> str:
        parser = cls()
        parser.feed(html_text)
        return "\n".join(parser.parts)
