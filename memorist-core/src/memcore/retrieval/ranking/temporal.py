from memcore.models import RetrievalPlan


def temporal_score(valid_from: str | None, valid_until: str | None, plan: RetrievalPlan) -> float:
    if plan.temporal_filter is None:
        return 1.0 if valid_until is None else 0.35
    if plan.temporal_filter.historical:
        if valid_until is not None:
            return 1.0
        if valid_from and plan.temporal_filter.normalized_from:
            return 0.8
        return 0.45
    return 1.0 if valid_until is None else 0.2
