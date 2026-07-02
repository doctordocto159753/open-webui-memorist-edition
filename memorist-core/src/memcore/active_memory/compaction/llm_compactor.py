from typing import Protocol

from memcore.active_memory.compaction.drift import unsupported_assertions
from memcore.active_memory.schemas import BlockSource


class LLMCompactor(Protocol):
    def compact(self, sources: list[BlockSource]) -> dict[str, object]: ...


def validate_llm_output(output: dict[str, object], sources: list[BlockSource]) -> list[str]:
    items = output.get("items")
    if not isinstance(items, list):
        return ["items must be a list"]
    unsupported = unsupported_assertions(
        [item for item in items if isinstance(item, dict)],
        sources,
    )
    return [f"unsupported assertion: {item}" for item in unsupported]
