from memcore.active_memory.schemas import BlockSource
from memcore.models import MemoryBlock


def deterministic_compact(
    block: MemoryBlock,
    sources: list[BlockSource],
) -> tuple[list[BlockSource], list[dict[str, object]]]:
    selected: list[BlockSource] = []
    omitted: list[dict[str, object]] = []
    seen_keys: dict[str, BlockSource] = {}
    for source in sources:
        previous = seen_keys.get(source.canonical_key)
        if previous is not None and previous.normalized_text == source.normalized_text:
            omitted.append(
                {
                    "memory_uuid": source.memory_uuid,
                    "memory_version_uuid": source.memory_version_uuid,
                    "reason": "duplicate_canonical_key",
                }
            )
            continue
        seen_keys.setdefault(source.canonical_key, source)
        selected.append(source)

    selected.sort(
        key=lambda source: (
            source.memory_type != "constraint",
            -source.confidence,
            source.canonical_key,
        )
    )
    return selected, omitted


def preserves_negation(text: str) -> bool:
    lowered = text.lower()
    return any(term in lowered for term in {"not", "never", "do not", "don't", "نباید"})
