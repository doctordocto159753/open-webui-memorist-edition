"""Privacy-aware display shaping for memory context attachments."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

DISPLAY_MODEL_VERSION = "pr5a-v1"
_MAX_SUMMARY_LENGTH = 280
_HIDDEN_SUMMARY = "Sensitive memory — content hidden"

_PACKET_SECTIONS = (
    "trusted_directives",
    "current_context",
    "relevant_memories",
    "historical_context",
    "conflicts_and_uncertainty",
)
_SECTION_REASONS = {
    "trusted_directives": "Matched a trusted directive for this turn",
    "current_context": "Current context for this turn",
    "relevant_memories": "Relevant to this turn",
    "historical_context": "Historical context for this turn",
    "conflicts_and_uncertainty": "Included to show a conflict or uncertainty",
}
_SENSITIVE_CLASSES = {"private", "restricted", "secret", "sensitive"}
_REVIEW_STATUSES = {"manual_review", "needs_review", "pending_review", "review"}
_METADATA_FIELDS = (
    "semantic_authority",
    "gate_decision",
    "route_type",
    "route_status",
    "route_uuid",
    "annotation_uuid",
    "analysis_run_uuid",
    "prompt_execution_uuid",
    "route_mapping_version",
    "candidate_service_version",
    "provenance_policy_version",
)
_SECRET_PATTERNS = (
    re.compile(r"(?i)\b(bearer\s+)[a-z0-9._~+/=-]{8,}"),
    re.compile(
        r"(?i)\b(api[_-]?key|access[_-]?token|token|password|secret)"
        r"\s*[:=]\s*([^\s,;]{4,})"
    ),
    re.compile(r"\bsk-[a-zA-Z0-9_-]{8,}\b"),
    re.compile(r"\beyJ[a-zA-Z0-9_-]{8,}\.[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\b"),
)


def _as_mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    if hasattr(value, "keys"):
        try:
            return {str(key): value[key] for key in value.keys()}
        except (KeyError, TypeError):
            return {}
    return {}


def _parse_json_mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return _as_mapping(parsed)


def redact_display_text(value: Any, *, sensitive: bool = False) -> str:
    """Return a compact display value without leaking common secret formats."""
    if sensitive:
        return _HIDDEN_SUMMARY

    text = " ".join(str(value or "").split())
    if not text:
        return "Memory details available"

    for pattern in _SECRET_PATTERNS:
        if pattern.groups >= 2:
            text = pattern.sub(lambda match: f"{match.group(1)}: [redacted]", text)
        elif pattern.groups == 1:
            text = pattern.sub(lambda match: f"{match.group(1)}[redacted]", text)
        else:
            text = pattern.sub("[redacted]", text)

    if len(text) > _MAX_SUMMARY_LENGTH:
        return f"{text[: _MAX_SUMMARY_LENGTH - 1].rstrip()}…"
    return text


def _iter_packet_items(packet: Mapping[str, Any]) -> list[tuple[str, Mapping[str, Any]]]:
    seen: set[tuple[str, str]] = set()
    result: list[tuple[str, Mapping[str, Any]]] = []
    for section in _PACKET_SECTIONS:
        values = packet.get(section)
        if not isinstance(values, list):
            continue
        for raw_item in values:
            item = _as_mapping(raw_item)
            memory_uuid = str(item.get("memory_uuid") or item.get("active_block_uuid") or "")
            version_uuid = str(item.get("memory_version_uuid") or "")
            key = (memory_uuid, version_uuid)
            if not memory_uuid or key in seen:
                continue
            seen.add(key)
            result.append((section, item))
    return result


def _fetch_memory_audit(
    connection: Any,
    memory_uuid: str,
    memory_version_uuid: str,
) -> Mapping[str, Any]:
    if not memory_uuid or not memory_version_uuid:
        return {}
    row = connection.execute(
        """
        SELECT
            m.memory_uuid,
            m.canonical_key,
            m.memory_type,
            m.scope_type,
            m.scope_uuid,
            m.status AS memory_status,
            m.sensitivity_class AS memory_sensitivity,
            m.created_at AS memory_created_at,
            m.updated_at AS memory_updated_at,
            mv.memory_version_uuid,
            mv.normalized_text,
            mv.confidence,
            mv.importance,
            mv.created_at AS version_created_at,
            mv.source_candidate_uuid,
            mc.candidate_uuid,
            mc.candidate_type,
            mc.source_authority,
            mc.explicitness,
            mc.status AS candidate_status,
            mc.sensitivity_class AS candidate_sensitivity,
            mc.extraction_metadata_ijson
        FROM memories AS m
        JOIN memory_versions AS mv ON mv.memory_uuid = m.memory_uuid
        LEFT JOIN memory_candidates AS mc
            ON mc.candidate_uuid = mv.source_candidate_uuid
        WHERE m.memory_uuid = ? AND mv.memory_version_uuid = ?
        """,
        (memory_uuid, memory_version_uuid),
    ).fetchone()
    return _as_mapping(row)


def _is_sensitive(packet_item: Mapping[str, Any], audit: Mapping[str, Any]) -> bool:
    sensitivity = str(
        audit.get("memory_sensitivity")
        or audit.get("candidate_sensitivity")
        or packet_item.get("sensitivity_class")
        or ""
    ).lower()
    status = str(
        audit.get("candidate_status")
        or audit.get("memory_status")
        or packet_item.get("status")
        or ""
    ).lower()
    confidence_label = str(packet_item.get("confidence_label") or "").lower()
    return (
        sensitivity in _SENSITIVE_CLASSES
        or status in _REVIEW_STATUSES
        or confidence_label in _REVIEW_STATUSES
    )


def _display_item(
    section: str,
    packet_item: Mapping[str, Any],
    audit: Mapping[str, Any],
) -> dict[str, Any]:
    metadata = _parse_json_mapping(audit.get("extraction_metadata_ijson"))
    sensitive = _is_sensitive(packet_item, audit)
    normalized_text = audit.get("normalized_text") or packet_item.get("normalized_text")
    summary = redact_display_text(normalized_text, sensitive=sensitive)
    memory_uuid = str(audit.get("memory_uuid") or packet_item.get("memory_uuid") or "")
    version_uuid = str(
        audit.get("memory_version_uuid") or packet_item.get("memory_version_uuid") or ""
    )
    source_candidate_uuid = audit.get("source_candidate_uuid") or audit.get("candidate_uuid")
    status = (
        audit.get("candidate_status")
        or audit.get("memory_status")
        or packet_item.get("status")
        or ("current" if packet_item.get("current") else "historical")
    )

    technical_ids = {
        "memory_uuid": memory_uuid or None,
        "memory_version_uuid": version_uuid or None,
        "source_candidate_uuid": source_candidate_uuid,
        "route_uuid": metadata.get("route_uuid"),
        "annotation_uuid": metadata.get("annotation_uuid"),
        "analysis_run_uuid": metadata.get("analysis_run_uuid"),
        "prompt_execution_uuid": metadata.get("prompt_execution_uuid"),
    }
    metadata_versions = {
        key: metadata.get(key)
        for key in (
            "route_mapping_version",
            "candidate_service_version",
            "provenance_policy_version",
        )
        if metadata.get(key) is not None
    }

    scope = _as_mapping(packet_item.get("scope"))
    scope_type = audit.get("scope_type") or scope.get("scope_type") or packet_item.get("scope_type")
    scope_uuid = audit.get("scope_uuid") or scope.get("scope_uuid") or packet_item.get("scope_uuid")

    return {
        "id": version_uuid or memory_uuid,
        "title": summary,
        "summary": summary,
        "memory_type": audit.get("memory_type") or packet_item.get("memory_type"),
        "scope_type": scope_type,
        "scope_uuid": scope_uuid,
        "source_authority": (
            audit.get("source_authority")
            or packet_item.get("source_authority_label")
            or metadata.get("source_authority")
        ),
        "explicitness": audit.get("explicitness") or metadata.get("explicitness"),
        "status": status,
        "confidence": audit.get("confidence") or packet_item.get("confidence_label"),
        "importance": audit.get("importance"),
        "retrieval_score": packet_item.get("retrieval_score"),
        "reason": _SECTION_REASONS.get(section, "Relevant to this turn"),
        "current": bool(packet_item.get("current", True)),
        "sensitive": sensitive,
        "created_at": audit.get("memory_created_at") or audit.get("version_created_at"),
        "updated_at": audit.get("memory_updated_at"),
        "semantic_authority": metadata.get("semantic_authority"),
        "gate_decision": metadata.get("gate_decision"),
        "route_type": metadata.get("route_type"),
        "route_status": metadata.get("route_status"),
        "metadata_versions": metadata_versions,
        "technical_ids": {key: value for key, value in technical_ids.items() if value},
    }


def build_attachment_display(connection: Any, attachment: Mapping[str, Any]) -> dict[str, Any]:
    """Build an allowlisted UI model from a stored attachment and canonical audit rows."""
    packet = _parse_json_mapping(attachment.get("ijson_attachment"))
    items: list[dict[str, Any]] = []
    for section, packet_item in _iter_packet_items(packet):
        memory_uuid = str(packet_item.get("memory_uuid") or "")
        version_uuid = str(packet_item.get("memory_version_uuid") or "")
        try:
            audit = _fetch_memory_audit(connection, memory_uuid, version_uuid)
        except Exception:
            # Active blocks and legacy packets may not point to a memory-version pair.
            audit = {}
        items.append(_display_item(section, packet_item, audit))

    return {
        "display_version": DISPLAY_MODEL_VERSION,
        "memory_count": len(items),
        "items": items,
    }
