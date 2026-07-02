from memcore.active_memory.schemas import BlockSource


def validate_coverage(
    selected_sources: list[BlockSource],
    rendered_text: str,
) -> list[str]:
    errors: list[str] = []
    selected_versions = {source.memory_version_uuid for source in selected_sources}
    if selected_sources and not selected_versions:
        errors.append("missing provenance mappings")
    active_constraints = [
        source for source in selected_sources if source.memory_type == "constraint"
    ]
    for source in active_constraints:
        if source.normalized_text not in rendered_text:
            errors.append("active safety constraint disappeared")
    return errors
