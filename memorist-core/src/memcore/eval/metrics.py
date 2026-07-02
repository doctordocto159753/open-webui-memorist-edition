from __future__ import annotations

import math
import statistics
from typing import Any


def recall_at_k(expected: list[str], actual: list[str], k: int) -> float:
    if not expected:
        return 1.0
    return len(set(expected) & set(actual[:k])) / len(set(expected))


def precision_at_k(expected: list[str], actual: list[str], k: int) -> float:
    if k <= 0:
        return 0.0
    sliced = actual[:k]
    if not sliced:
        return 1.0 if not expected else 0.0
    return len(set(expected) & set(sliced)) / len(sliced)


def mrr(expected: list[str], actual: list[str]) -> float:
    expected_set = set(expected)
    for index, key in enumerate(actual, start=1):
        if key in expected_set:
            return 1.0 / index
    return 0.0 if expected else 1.0


def ndcg_at_k(expected: list[str], actual: list[str], k: int) -> float:
    expected_set = set(expected)
    dcg = sum(
        1.0 / math.log2(index + 2) for index, key in enumerate(actual[:k]) if key in expected_set
    )
    ideal = sum(1.0 / math.log2(index + 2) for index in range(min(len(expected_set), k)))
    return 1.0 if ideal == 0 else dcg / ideal


def percentile(values: list[float], percentile_value: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = math.ceil((percentile_value / 100) * len(ordered)) - 1
    return ordered[min(len(ordered) - 1, max(0, index))]


def summarize_case_metrics(case_results: list[dict[str, Any]], k: int = 5) -> dict[str, Any]:
    expected_abstentions = [item for item in case_results if item["expected_abstention"]]
    actual_abstentions = [item for item in case_results if item["actual_abstention"]]
    correct_abstentions = [
        item for item in case_results if item["expected_abstention"] and item["actual_abstention"]
    ]
    latencies = [float(item["latency_ms"]) for item in case_results]
    return {
        "recall_at_k": _mean(case_results, "recall_at_k"),
        "precision_at_k": _mean(case_results, "precision_at_k"),
        "mrr": _mean(case_results, "mrr"),
        "ndcg_at_k": _mean(case_results, "ndcg_at_k"),
        "abstention_precision": _ratio(correct_abstentions, actual_abstentions),
        "abstention_recall": _ratio(correct_abstentions, expected_abstentions),
        "current_vs_stale_accuracy": _accuracy(case_results, "current_vs_stale_ok"),
        "historical_version_accuracy": _accuracy(case_results, "historical_version_ok"),
        "conflict_detection_accuracy": _accuracy(case_results, "conflict_detection_ok"),
        "scope_leak_count": _sum(case_results, "scope_leak_count"),
        "forbidden_memory_injection_count": _sum(case_results, "forbidden_memory_injection_count"),
        "attachment_token_usage": _sum(case_results, "attachment_token_usage"),
        "attachment_budget_violation_count": _sum(case_results, "attachment_budget_violation"),
        "p50_preflight_latency_ms": percentile(latencies, 50),
        "p95_preflight_latency_ms": percentile(latencies, 95),
        "k": k,
    }


def _mean(case_results: list[dict[str, Any]], field: str) -> float:
    return statistics.fmean(float(item[field]) for item in case_results) if case_results else 0.0


def _sum(case_results: list[dict[str, Any]], field: str) -> int:
    return sum(int(item[field]) for item in case_results)


def _ratio(numerator: list[dict[str, Any]], denominator: list[dict[str, Any]]) -> float:
    return len(numerator) / len(denominator) if denominator else 1.0


def _accuracy(case_results: list[dict[str, Any]], field: str) -> float:
    if not case_results:
        return 1.0
    return statistics.fmean(1.0 if item[field] else 0.0 for item in case_results)
