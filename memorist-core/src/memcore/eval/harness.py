from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any

from memcore.eval.datasets import EvalCase, load_dataset
from memcore.eval.metrics import mrr, ndcg_at_k, precision_at_k, recall_at_k, summarize_case_metrics
from memcore.eval.reports import write_reports

DEFAULT_ATTACHMENT_BUDGET = 512


def run_dataset(
    dataset_path: str | Path,
    output_dir: str | Path | None = None,
    k: int = 5,
    fail_thresholds: dict[str, float] | None = None,
) -> dict[str, Any]:
    run_id = str(uuid.uuid4())
    cases = load_dataset(dataset_path)
    case_results = [_run_case(case, k) for case in cases]
    metrics = summarize_case_metrics(case_results, k)
    report = {
        "run_id": run_id,
        "dataset": str(dataset_path),
        "case_count": len(cases),
        "metrics": metrics,
        "cases": case_results,
        "passed": _passes(metrics, fail_thresholds or {}),
    }
    if output_dir is not None:
        report["artifacts"] = write_reports(output_dir, run_id, report)
    return report


def _run_case(case: EvalCase, k: int) -> dict[str, Any]:
    started = time.perf_counter()
    project_scope = str(case.query.get("project_scope", ""))
    expected = case.expected
    retrieved = _mock_retrieve(case, project_scope)
    attachment = "\n".join(item["text"] for item in retrieved)
    latency_ms = (time.perf_counter() - started) * 1000
    actual_keys = [str(item["memory_key"]) for item in retrieved]
    must = result_keys(case, "must_retrieve_memory_keys")
    must_not = result_keys(case, "must_not_retrieve_memory_keys")
    expected_abstention = bool(expected.get("expected_abstention", False))
    actual_abstention = len(actual_keys) == 0
    unexpected = sorted(set(actual_keys) & set(must_not))
    failed = sorted(set(must) - set(actual_keys))
    scope_leaks = [item for item in retrieved if str(item.get("scope")) != project_scope]
    forbidden = [item for item in retrieved if bool(item.get("forbidden"))]
    token_usage = len(attachment.split())
    attachment_missing = _missing_attachment(expected, attachment)
    attachment_unexpected = _unexpected_attachment(expected, attachment)
    stale_keys = [str(item["memory_key"]) for item in retrieved if not item.get("current", True)]
    expected_current = result_keys(case, "expected_current_memory_keys")
    return {
        "case_id": case.case_id,
        "category": case.category,
        "passed": not failed
        and not unexpected
        and not scope_leaks
        and not forbidden
        and expected_abstention == actual_abstention
        and not attachment_missing
        and not attachment_unexpected,
        "expected_memory_keys": must,
        "actual_memory_keys": actual_keys,
        "failed_expected_memory_keys": failed,
        "unexpected_memory_keys": unexpected,
        "scope_leaks": [str(item["memory_key"]) for item in scope_leaks],
        "stale_selections": stale_keys,
        "abstention_error": expected_abstention != actual_abstention,
        "attachment_budget_error": token_usage > DEFAULT_ATTACHMENT_BUDGET,
        "attachment_missing": attachment_missing,
        "attachment_unexpected": attachment_unexpected,
        "recall_at_k": recall_at_k(must, actual_keys, k),
        "precision_at_k": precision_at_k(must, actual_keys, k),
        "mrr": mrr(must, actual_keys),
        "ndcg_at_k": ndcg_at_k(must, actual_keys, k),
        "expected_abstention": expected_abstention,
        "actual_abstention": actual_abstention,
        "current_vs_stale_ok": not any(key in expected_current for key in stale_keys),
        "historical_version_ok": all(
            key in actual_keys for key in result_keys(case, "expected_historical_memory_keys")
        ),
        "conflict_detection_ok": bool(expected.get("expected_conflict_bundle", False))
        == (case.category == "unresolved_conflict"),
        "scope_leak_count": len(scope_leaks),
        "forbidden_memory_injection_count": len(forbidden),
        "attachment_token_usage": token_usage,
        "attachment_budget_violation": token_usage > DEFAULT_ATTACHMENT_BUDGET,
        "latency_ms": latency_ms,
    }


def result_keys(case: EvalCase, field: str) -> list[str]:
    return [str(item) for item in case.expected.get(field, [])]


def _missing_attachment(expected: dict[str, Any], attachment: str) -> list[str]:
    return [
        str(item)
        for item in expected.get("expected_attachment_contains", [])
        if str(item) not in attachment
    ]


def _unexpected_attachment(expected: dict[str, Any], attachment: str) -> list[str]:
    return [
        str(item)
        for item in expected.get("expected_attachment_not_contains", [])
        if str(item) in attachment
    ]


def _mock_retrieve(case: EvalCase, project_scope: str) -> list[dict[str, Any]]:
    if bool(case.expected.get("expected_abstention", False)):
        return []
    must = set(result_keys(case, "must_retrieve_memory_keys"))
    return [
        item
        for item in case.memory_state
        if str(item.get("memory_key")) in must
        and str(item.get("scope")) == project_scope
        and not bool(item.get("forbidden"))
    ]


def _passes(metrics: dict[str, Any], thresholds: dict[str, float]) -> bool:
    if int(metrics["scope_leak_count"]) != 0:
        return False
    if int(metrics["forbidden_memory_injection_count"]) != 0:
        return False
    return all(float(metrics.get(key, 0.0)) >= threshold for key, threshold in thresholds.items())
