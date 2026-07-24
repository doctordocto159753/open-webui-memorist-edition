from __future__ import annotations

from typing import Any

from memcore.active_memory.renderer import token_count
from memcore.active_memory.schemas import BlockSource, ValidatedCompactionProposal
from memcore.models import MemoryBlock
from memcore.validators.ijson import canonical_hash_ijson

COMPACTION_POLICY_VERSION = "provider-grounded-v3"
EXCLUSION_REASONS = {"superseded", "duplicate", "char_limit", "lower_priority"}


def validate_and_render_proposal(
    block: MemoryBlock,
    sources: list[BlockSource],
    output: dict[str, Any],
) -> ValidatedCompactionProposal:
    by_version = {source.memory_version_uuid: source for source in sources}
    if any(
        source.scope_type != block.scope_type or source.scope_uuid != block.scope_uuid
        for source in sources
    ):
        raise ValueError("compaction source escaped the block privacy scope")
    if any(source.sensitivity_class == "secret" for source in sources):
        raise ValueError("secret source cannot enter an active memory block")

    included: list[BlockSource] = []
    included_ids: set[str] = set()
    rendered_items: list[dict[str, Any]] = []
    for item in output.get("items", []):
        version_ids = _string_list(item.get("source_memory_version_uuids"))
        memory_ids = set(_string_list(item.get("source_memory_uuids")))
        if not version_ids:
            raise ValueError("compaction item has no source version")
        item_sources: list[BlockSource] = []
        for version_uuid in version_ids:
            source = by_version.get(version_uuid)
            if source is None:
                raise ValueError("compaction referenced an unsupported source UUID")
            if source.memory_uuid not in memory_ids:
                raise ValueError("compaction memory/version provenance mismatch")
            if version_uuid in included_ids:
                raise ValueError("compaction source version was included more than once")
            item_sources.append(source)
            included.append(source)
            included_ids.add(version_uuid)
        text = str(item["text"]).strip()
        _require_grounded_text(text, item_sources)
        rendered_items.append(
            {
                "text": text,
                "source_memory_uuids": [source.memory_uuid for source in item_sources],
                "source_memory_version_uuids": [
                    source.memory_version_uuid for source in item_sources
                ],
                "source_provenance": [
                    {
                        "memory_uuid": source.memory_uuid,
                        "memory_version_uuid": source.memory_version_uuid,
                        "source_authority": source.source_authority,
                    }
                    for source in item_sources
                ],
            }
        )

    excluded_ids: set[str] = set()
    omitted: list[dict[str, Any]] = []
    for item in output.get("excluded_memory_version_uuids", []):
        version_uuid = str(item.get("memory_version_uuid") or "")
        reason = str(item.get("reason_code") or "")
        source = by_version.get(version_uuid)
        if source is None:
            raise ValueError("compaction excluded an unsupported source UUID")
        if reason not in EXCLUSION_REASONS:
            raise ValueError("compaction exclusion reason is not allowed")
        if source.memory_type == "constraint":
            raise ValueError("mandatory constraint source cannot be omitted")
        if version_uuid in included_ids or version_uuid in excluded_ids:
            raise ValueError("compaction source has conflicting inclusion state")
        excluded_ids.add(version_uuid)
        omitted.append(
            {
                "memory_uuid": source.memory_uuid,
                "memory_version_uuid": version_uuid,
                "reason": reason,
            }
        )

    all_ids = set(by_version)
    if included_ids | excluded_ids != all_ids:
        raise ValueError("compaction omitted a source without an explicit decision")

    conflicts = _validate_conflicts(output.get("conflicts", []), by_version)
    _require_conflict_preservation(included, conflicts)
    summary = str(output.get("summary") or "").strip()
    if summary:
        _require_grounded_text(summary, included)

    lines = [f"{block.block_type.value}: {block.label}"]
    if summary:
        lines.append(f"- Summary: {summary}")
    for item in rendered_items:
        lines.append(f"- {item['text']}")
    value = "\n".join(lines)
    if len(value) > block.char_limit:
        raise ValueError("provider compaction exceeds the block character budget")

    return ValidatedCompactionProposal(
        value=value,
        sources=included,
        omitted_sources=omitted,
        structured_value={
            "content_source": "provider",
            "policy_version": COMPACTION_POLICY_VERSION,
            "summary": summary or None,
            "items": rendered_items,
            "omitted_sources": omitted,
            "conflicts": conflicts,
            "token_count": token_count(value),
        },
        output_hash=canonical_hash_ijson(output),
    )


def _require_grounded_text(text: str, sources: list[BlockSource]) -> None:
    allowed = {_normalized(source.normalized_text) for source in sources}
    claims = [
        _normalized(line.removeprefix("-").strip())
        for line in text.splitlines()
        if line.strip()
    ]
    if not claims or any(claim not in allowed for claim in claims):
        raise ValueError("provider compaction introduced an unsupported claim")


def _validate_conflicts(
    values: object,
    by_version: dict[str, BlockSource],
) -> list[dict[str, Any]]:
    if not isinstance(values, list):
        raise ValueError("compaction conflicts must be a list")
    result: list[dict[str, Any]] = []
    for value in values:
        if not isinstance(value, dict):
            raise ValueError("compaction conflict must be an object")
        version_ids = _string_list(value.get("memory_version_uuids"))
        if len(version_ids) < 2 or any(item not in by_version for item in version_ids):
            raise ValueError("compaction conflict provenance is invalid")
        if value.get("status") != "unresolved":
            raise ValueError("compaction conflicts must remain unresolved")
        result.append(
            {"memory_version_uuids": version_ids, "status": "unresolved"}
        )
    return result


def _require_conflict_preservation(
    sources: list[BlockSource],
    conflicts: list[dict[str, Any]],
) -> None:
    by_key: dict[str, list[BlockSource]] = {}
    for source in sources:
        by_key.setdefault(source.canonical_key, []).append(source)
    declared = [
        set(str(item) for item in conflict["memory_version_uuids"])
        for conflict in conflicts
    ]
    for grouped_sources in by_key.values():
        source_ids = {source.memory_version_uuid for source in grouped_sources}
        values = {_normalized(source.normalized_text) for source in grouped_sources}
        if len(source_ids) > 1 and len(values) > 1 and not any(
            source_ids.issubset(conflict_ids) for conflict_ids in declared
        ):
            raise ValueError("compaction silently flattened an unresolved conflict")


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError("compaction provenance must be a string list")
    return [str(item) for item in value]


def _normalized(value: str) -> str:
    return " ".join(value.split())
