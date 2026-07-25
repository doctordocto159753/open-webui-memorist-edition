from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unicodedata import decimal

from memcore.models import Explicitness, SourceAuthority


@dataclass(frozen=True)
class StructuredProjectArtifact:
    normalized_text: str
    source_authority: SourceAuthority
    explicitness: Explicitness
    confidence: float
    importance: float
    object_payload: dict[str, Any]
    metadata: dict[str, Any]


def structured_project_artifact(
    *,
    raw_text: str,
    message_role: str,
    list_item_count: int,
    has_cross_session_scope: bool,
    preceding_user_message_uuid: str | None,
) -> StructuredProjectArtifact | None:
    """Recognize substantial structured output without asserting it as a user fact.

    This is shape-based continuity handling, not topic keyword extraction.  It
    preserves the complete project artifact and its authority so later retrieval
    can cite the original plan, specification, report, or checklist.
    """

    text = raw_text.strip()
    structural_items = max(list_item_count, _numbered_line_count(text))
    if not has_cross_session_scope or len(text) < 200 or structural_items < 3:
        return None
    if message_role not in {"user", "assistant"}:
        return None

    assistant_authored = message_role == "assistant"
    authority = (
        SourceAuthority.ASSISTANT_CLAIM if assistant_authored else SourceAuthority.USER_EXPLICIT
    )
    artifact_kind = (
        "assistant_produced_project_artifact"
        if assistant_authored
        else "user_authored_project_artifact"
    )
    return StructuredProjectArtifact(
        normalized_text=text,
        source_authority=authority,
        explicitness=(Explicitness.INFERRED if assistant_authored else Explicitness.EXPLICIT),
        confidence=0.62 if assistant_authored else 0.9,
        importance=0.82,
        object_payload={
            "value": text,
            "artifact_kind": artifact_kind,
            "preceding_user_message_uuid": preceding_user_message_uuid,
        },
        metadata={
            "artifact_kind": artifact_kind,
            "authority_label": (
                "earlier_assistant_produced_project_artifact"
                if assistant_authored
                else "user_authored_project_artifact"
            ),
            "not_user_fact": assistant_authored,
            "cross_session_eligible": True,
            "preceding_user_message_uuid": preceding_user_message_uuid,
            "structured_list_item_count": structural_items,
            "semantic_authority": "structured_project_artifact",
            "candidate_service_version": "structured-project-artifact-v1",
            "requires_high_confidence_pass": assistant_authored,
        },
    )


def _numbered_line_count(text: str) -> int:
    count = 0
    for line in text.splitlines():
        prefix = line.lstrip()
        digits = 0
        for character in prefix:
            try:
                decimal(character)
            except (TypeError, ValueError):
                break
            digits += 1
        if digits and prefix[digits : digits + 1] in {".", ")", ":", "-", "–"}:
            count += 1
    return count
