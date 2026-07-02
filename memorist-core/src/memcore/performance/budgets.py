from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class RuntimeBudget:
    profile: str
    max_preflight_latency_ms: int
    max_attachment_tokens: int
    max_jobs_per_minute: int
    max_import_batch_size: int
    max_memory_worker_concurrency: int
    max_graph_projection_batch_size: int
    max_sqlite_db_size_warning: int
    max_object_store_size_warning: int
    max_import_archive_size: int
    max_expanded_import_size: int
    lexical_only: bool
    graph_required: bool
    embedding_required: bool


PROFILES: dict[str, RuntimeBudget] = {
    "lite": RuntimeBudget(
        "lite",
        800,
        900,
        30,
        100,
        1,
        0,
        512_000_000,
        2_000_000_000,
        50_000_000,
        250_000_000,
        True,
        False,
        False,
    ),
    "standard": RuntimeBudget(
        "standard",
        1200,
        1800,
        120,
        500,
        2,
        100,
        2_000_000_000,
        10_000_000_000,
        250_000_000,
        1_000_000_000,
        False,
        False,
        False,
    ),
    "full": RuntimeBudget(
        "full",
        1800,
        4000,
        600,
        2000,
        4,
        1000,
        10_000_000_000,
        50_000_000_000,
        1_000_000_000,
        5_000_000_000,
        False,
        True,
        True,
    ),
}


def get_budget(profile: str) -> RuntimeBudget:
    try:
        return PROFILES[profile]
    except KeyError as exc:
        raise ValueError(f"unknown runtime profile: {profile}") from exc


def budget_dict(profile: str) -> dict[str, object]:
    return asdict(get_budget(profile))
