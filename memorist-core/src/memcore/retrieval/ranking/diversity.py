from memcore.models import ScoredMemoryItem


def diversify(items: list[ScoredMemoryItem], limit: int) -> list[ScoredMemoryItem]:
    selected: list[ScoredMemoryItem] = []
    seen_keys: set[str] = set()
    for item in items:
        key = f"{item.memory_type}:{item.scope_type}:{item.normalized_text.lower()}"
        if key in seen_keys and item.memory_type not in {"constraint", "decision"}:
            continue
        seen_keys.add(key)
        selected.append(item)
        if len(selected) >= limit:
            break
    return selected
