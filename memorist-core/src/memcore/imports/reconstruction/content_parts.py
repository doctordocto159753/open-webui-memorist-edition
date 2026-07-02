from memcore.imports.reconstruction.models import ImportedContentPart


def normalize_content_part(value: object) -> ImportedContentPart:
    if isinstance(value, dict):
        part_type = str(value.get("part_type") or value.get("type") or "unknown")
        if "reasoning" in part_type.lower():
            return ImportedContentPart(
                part_type="provider_internal_reasoning", raw=value, quarantined=True
            )
        return ImportedContentPart(part_type=part_type, text=value.get("text"), raw=value)
    return ImportedContentPart(part_type="text", text=str(value))


def visible_text(parts: list[ImportedContentPart]) -> str:
    return "\n".join(part.text or "" for part in parts if not part.quarantined).strip()
