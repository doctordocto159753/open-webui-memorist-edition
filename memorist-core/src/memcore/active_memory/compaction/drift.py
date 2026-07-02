from memcore.active_memory.schemas import BlockSource


def unsupported_assertions(
    compacted_items: list[dict[str, object]],
    allowed_sources: list[BlockSource],
) -> list[str]:
    allowed_versions = {source.memory_version_uuid for source in allowed_sources}
    unsupported: list[str] = []
    for item in compacted_items:
        versions = item.get("source_memory_version_uuids")
        if not isinstance(versions, list) or not versions:
            unsupported.append(str(item.get("text", "")))
            continue
        if any(str(version) not in allowed_versions for version in versions):
            unsupported.append(str(item.get("text", "")))
    return unsupported
