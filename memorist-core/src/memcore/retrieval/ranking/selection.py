from memcore.models import ScoredMemoryItem
from memcore.retrieval.ranking.diversity import diversify


class SelectionResult:
    def __init__(
        self,
        selected: list[ScoredMemoryItem],
        abstention_status: str | None = None,
        abstention_reason: str | None = None,
    ) -> None:
        self.selected = selected
        self.abstention_status = abstention_status
        self.abstention_reason = abstention_reason


def select_items(
    items: list[ScoredMemoryItem],
    threshold: float = 0.18,
    limit: int = 8,
) -> SelectionResult:
    eligible = [item for item in items if item.final_score >= threshold]
    if not eligible:
        return SelectionResult([], "abstained", "no_candidate_passed_threshold")
    if any(item.conflict_status == "unresolved" for item in eligible[:2]):
        return SelectionResult(
            diversify(eligible, limit),
            "abstained",
            "unresolved_conflict_affects_answer",
        )
    return SelectionResult(diversify(eligible, limit))
