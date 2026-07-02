from memcore.imports.reconstruction.models import ImportedRole


def normalize_role(value: object) -> ImportedRole:
    normalized = str(value or "").lower()
    mapping = {
        "human": ImportedRole.USER,
        "user": ImportedRole.USER,
        "assistant": ImportedRole.ASSISTANT,
        "ai": ImportedRole.ASSISTANT,
        "system": ImportedRole.SYSTEM,
        "tool": ImportedRole.TOOL,
        "developer": ImportedRole.DEVELOPER,
    }
    return mapping.get(normalized, ImportedRole.UNKNOWN)
