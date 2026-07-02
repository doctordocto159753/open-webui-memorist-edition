from memcore.active_memory.schemas import BlockSource
from memcore.attachments.budget import conservative_token_count
from memcore.models import MemoryBlock


def render_block(
    block: MemoryBlock,
    sources: list[BlockSource],
    hot_cache_items: list[dict[str, object]],
) -> tuple[str, list[dict[str, object]]]:
    lines: list[str] = [f"{block.block_type.value}: {block.label}"]
    omitted: list[dict[str, object]] = []

    for item in hot_cache_items:
        line = f"- {item['label']}: {item['text']}"
        if _would_exceed(block, lines, line):
            omitted.append({"source": item["label"], "reason": "char_limit"})
            continue
        lines.append(line)

    for source in sources:
        line = f"- {source.normalized_text}"
        if _would_exceed(block, lines, line):
            omitted.append(
                {
                    "memory_uuid": source.memory_uuid,
                    "memory_version_uuid": source.memory_version_uuid,
                    "reason": "char_limit",
                }
            )
            continue
        lines.append(line)
    return "\n".join(lines), omitted


def structured_block(
    sources: list[BlockSource],
    hot_cache_items: list[dict[str, object]],
    omitted_sources: list[dict[str, object]],
) -> dict[str, object]:
    return {
        "items": [_structured_source(source) for source in sources],
        "hot_cache_items": hot_cache_items,
        "omitted_sources": omitted_sources,
    }


def _structured_source(source: BlockSource) -> dict[str, object]:
    item: dict[str, object] = {
        "text": source.normalized_text,
        "memory_uuid": source.memory_uuid,
        "memory_version_uuid": source.memory_version_uuid,
        "memory_type": source.memory_type,
        "source_authority": source.source_authority,
        "current": source.current,
        "confidence": source.confidence,
        "user_confirmed": source.user_confirmed,
        "sensitivity_class": source.sensitivity_class,
    }
    if source.valid_from is not None:
        item["valid_from"] = source.valid_from
    if source.valid_until is not None:
        item["valid_until"] = source.valid_until
    return item


def _would_exceed(block: MemoryBlock, current_lines: list[str], line: str) -> bool:
    candidate = "\n".join([*current_lines, line])
    return len(candidate) > block.char_limit


def token_count(text: str) -> int:
    return conservative_token_count(text)
