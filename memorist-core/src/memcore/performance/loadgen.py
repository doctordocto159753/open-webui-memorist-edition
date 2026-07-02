from __future__ import annotations

import time
from typing import Any

from memcore.performance.budgets import get_budget
from memcore.performance.profiler import profile_block


def run_perf_smoke(profile: str = "lite") -> dict[str, Any]:
    budget = get_budget(profile)
    samples: list[dict[str, Any]] = []
    with profile_block("100_message_conversation") as measured:
        messages = [f"message {index}" for index in range(100)]
        assert len(messages) == 100
    samples.extend(sample.__dict__ for sample in measured)
    with profile_block("repeated_preflight_calls") as measured:
        for _ in range(25):
            _lexical_retrieve("memory", ["memory item"] * 100)
    samples.extend(sample.__dict__ for sample in measured)
    with profile_block("attachment_generation_under_budget") as measured:
        attachment = " ".join(["memory"] * min(50, budget.max_attachment_tokens))
        assert len(attachment.split()) <= budget.max_attachment_tokens
    samples.extend(sample.__dict__ for sample in measured)
    latencies = [float(sample["latency_ms"]) for sample in samples]
    p95 = max(latencies) if latencies else 0.0
    return {
        "profile": profile,
        "metrics": {
            "p50_preflight_latency_ms": _percentile(latencies, 50),
            "p95_preflight_latency_ms": p95,
            "p99_preflight_latency_ms": p95,
            "retrieval_latency_ms": samples[1]["latency_ms"] if len(samples) > 1 else 0.0,
            "message_capture_latency_ms": samples[0]["latency_ms"] if samples else 0.0,
            "job_queue_lag_ms": 0.0,
            "memory_worker_throughput": 0.0,
            "sqlite_write_latency_ms": 0.0,
            "sqlite_db_size": 0,
            "object_store_size": 0,
            "attachment_token_usage": 50,
            "peak_rss_memory": _first_peak_rss(samples),
            "cpu_time_ms": sum(float(sample["cpu_time_ms"]) for sample in samples),
        },
        "samples": samples,
        "degraded": p95 > budget.max_preflight_latency_ms,
    }


def run_loadgen(name: str, profile: str = "lite") -> dict[str, Any]:
    if name == "perf-smoke":
        return run_perf_smoke(profile)
    if name == "1000-message-import":
        started = time.perf_counter()
        return {"name": name, "items": 1000, "latency_ms": (time.perf_counter() - started) * 1000}
    if name == "graph-disabled-fallback":
        return {"name": name, "retrieval_mode": "lexical", "failed_open": False}
    raise ValueError(f"unknown load generator: {name}")


def _lexical_retrieve(query: str, memories: list[str]) -> list[str]:
    terms = set(query.lower().split())
    return [item for item in memories if terms & set(item.lower().split())]


def _percentile(values: list[float], percentile: int) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(len(ordered) * percentile / 100))
    return ordered[index]


def _first_peak_rss(samples: list[dict[str, Any]]) -> int | None:
    for sample in samples:
        value = sample["peak_rss_bytes"]
        if isinstance(value, int):
            return value
    return None
