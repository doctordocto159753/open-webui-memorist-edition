from typing import Protocol

from memcore.models import ScoredMemoryItem


class Reranker(Protocol):
    def rerank(self, items: list[ScoredMemoryItem]) -> list[ScoredMemoryItem]: ...


class NoopReranker:
    def rerank(self, items: list[ScoredMemoryItem]) -> list[ScoredMemoryItem]:
        return items


def safe_rerank(
    items: list[ScoredMemoryItem],
    reranker: Reranker | None = None,
) -> list[ScoredMemoryItem]:
    if reranker is None:
        return items
    original_versions = {item.memory_version_uuid for item in items}
    reranked = reranker.rerank(items)
    if {item.memory_version_uuid for item in reranked} - original_versions:
        raise ValueError("reranker cannot introduce new memory version UUIDs")
    return reranked
