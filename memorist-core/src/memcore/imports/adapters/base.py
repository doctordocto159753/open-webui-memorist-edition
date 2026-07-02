import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from memcore.imports.models import (
    FormatProbe,
    ImportIssue,
    ImportRecord,
    ImportValidationReport,
    StagedArtifact,
)


class JsonAdapterMixin:
    adapter_id = "base"
    adapter_version = "1"

    def validate(self, records: list[ImportRecord]) -> ImportValidationReport:
        issues: list[ImportIssue] = []
        for record in records:
            if not record.normalized_payload:
                issues.append(
                    ImportIssue(
                        severity="error",
                        issue_code="empty_normalized_payload",
                        message="Record produced an empty normalized payload",
                        source_record_id=record.source_record_id,
                    )
                )
        return ImportValidationReport(
            issues=issues,
            warning_count=sum(1 for issue in issues if issue.severity == "warning"),
            error_count=sum(1 for issue in issues if issue.severity == "error"),
        )

    def _json_artifacts(
        self, artifacts: list[StagedArtifact]
    ) -> Iterator[tuple[StagedArtifact, Any]]:
        for artifact in artifacts:
            if not artifact.object_store_path.lower().endswith((".json", ".jsonl")):
                continue
            path = Path(artifact.object_store_path)
            try:
                if path.suffix.lower() == ".jsonl":
                    rows = [
                        json.loads(line)
                        for line in path.read_text(encoding="utf-8").splitlines()
                        if line.strip()
                    ]
                    yield artifact, rows
                else:
                    yield artifact, json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue


def no_match(adapter_id: str, adapter_version: str) -> FormatProbe:
    return FormatProbe(
        adapter_id=adapter_id,
        adapter_version=adapter_version,
        confidence=0.0,
        detected_format="unknown",
    )


def text_part(text: str) -> dict[str, object]:
    return {"part_type": "text", "text": text}
