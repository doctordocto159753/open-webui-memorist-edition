from __future__ import annotations

import json
from typing import Any

from memcore.attachments.display import build_attachment_display, redact_display_text


class _Result:
    def __init__(self, row: dict[str, Any] | None) -> None:
        self.row = row

    def fetchone(self) -> dict[str, Any] | None:
        return self.row


class _Connection:
    def __init__(self, rows: dict[tuple[str, str], dict[str, Any]]) -> None:
        self.rows = rows

    def execute(self, _query: str, params: tuple[str, str]) -> _Result:
        return _Result(self.rows.get(params))


def _packet_item(memory_uuid: str, version_uuid: str, text: str) -> dict[str, Any]:
    return {
        "memory_uuid": memory_uuid,
        "memory_version_uuid": version_uuid,
        "memory_type": "preference",
        "scope": {"scope_type": "project", "scope_uuid": "project-1"},
        "normalized_text": text,
        "current": True,
        "confidence_label": "high",
        "source_authority_label": "user_explicit",
        "retrieval_score": 0.91,
        "evidence_text": "raw evidence must not be exposed",
        "conflict_status": "none",
    }


def test_build_attachment_display_is_allowlisted_deduplicated_and_auditable() -> None:
    first = _packet_item("memory-1", "version-1", "API key=supersecret")
    second = _packet_item("memory-2", "version-2", "private medical detail")
    attachment = {
        "ijson_attachment": {
            "current_context": [first, second],
            "relevant_memories": [first],
        }
    }
    metadata = {
        "semantic_authority": "canonical_jakobson_gate_route",
        "gate_decision": "analyze",
        "route_type": "preference",
        "route_status": "selected",
        "route_uuid": "route-1",
        "annotation_uuid": "annotation-1",
        "analysis_run_uuid": "analysis-1",
        "prompt_execution_uuid": "prompt-1",
        "route_mapping_version": "route-v1",
        "candidate_service_version": "candidate-v1",
        "provenance_policy_version": "provenance-v1",
    }
    connection = _Connection(
        {
            ("memory-1", "version-1"): {
                "memory_uuid": "memory-1",
                "memory_version_uuid": "version-1",
                "canonical_key": "preference:editor",
                "memory_type": "preference",
                "scope_type": "project",
                "scope_uuid": "project-1",
                "memory_status": "active",
                "memory_sensitivity": "normal",
                "memory_created_at": "2026-01-01T10:00:00+00:00",
                "memory_updated_at": "2026-01-02T10:00:00+00:00",
                "normalized_text": "API key=supersecret",
                "confidence": 0.88,
                "importance": 0.7,
                "source_candidate_uuid": "candidate-1",
                "candidate_uuid": "candidate-1",
                "source_authority": "user_explicit",
                "explicitness": "explicit",
                "candidate_status": "accepted",
                "candidate_sensitivity": "normal",
                "extraction_metadata_ijson": json.dumps(metadata),
            },
            ("memory-2", "version-2"): {
                "memory_uuid": "memory-2",
                "memory_version_uuid": "version-2",
                "memory_type": "fact",
                "scope_type": "user",
                "scope_uuid": "user-1",
                "memory_status": "active",
                "memory_sensitivity": "sensitive",
                "normalized_text": "private medical detail",
                "source_authority": "imported_record",
                "explicitness": "imported",
                "candidate_status": "accepted",
                "candidate_sensitivity": "sensitive",
                "extraction_metadata_ijson": "{}",
            },
        }
    )

    display = build_attachment_display(connection, attachment)

    assert display["display_version"] == "pr5a-v1"
    assert display["memory_count"] == 2
    normal, sensitive = display["items"]
    assert "supersecret" not in normal["summary"]
    assert normal["summary"] == "API key: [redacted]"
    assert normal["scope_type"] == "project"
    assert normal["source_authority"] == "user_explicit"
    assert normal["retrieval_score"] == 0.91
    assert normal["gate_decision"] == "analyze"
    assert normal["metadata_versions"]["provenance_policy_version"] == "provenance-v1"
    assert normal["technical_ids"]["route_uuid"] == "route-1"
    assert "evidence_text" not in normal
    assert "extraction_metadata_ijson" not in normal
    assert sensitive["sensitive"] is True
    assert sensitive["summary"] == "Sensitive memory — content hidden"
    assert "private medical detail" not in str(display)


def test_redaction_masks_common_credentials_and_limits_length() -> None:
    value = "Bearer abcdefghijkl password=hunter2 " + ("x" * 400)

    redacted = redact_display_text(value)

    assert "abcdefghijkl" not in redacted
    assert "hunter2" not in redacted
    assert "[redacted]" in redacted
    assert len(redacted) <= 280
