from __future__ import annotations

from pathlib import Path

from memcore.eval.harness import run_dataset
from memcore.performance.budgets import budget_dict
from memcore.performance.loadgen import run_perf_smoke
from memcore.reliability.backup import backup_sqlite
from memcore.reliability.consistency import run_consistency_check
from memcore.reliability.storage_maintenance import secure_delete_check, wal_checkpoint
from memcore.storage.migrations import apply_migrations
from memcore.storage.sqlite import connect


def test_eval_fixture_has_30_cases_and_no_scope_leaks(tmp_path: Path) -> None:
    report = run_dataset("src/memcore/eval/fixtures/basic.ijsonl", output_dir=tmp_path)
    assert report["case_count"] >= 30
    assert report["metrics"]["scope_leak_count"] == 0
    assert report["metrics"]["forbidden_memory_injection_count"] == 0
    assert (tmp_path / "eval-report.ijson").exists()
    assert (tmp_path / "eval-report.md").exists()


def test_performance_lite_budget_and_smoke() -> None:
    lite = budget_dict("lite")
    assert lite["graph_required"] is False
    assert lite["embedding_required"] is False
    assert lite["lexical_only"] is True
    report = run_perf_smoke("lite")
    assert report["profile"] == "lite"
    assert "p95_preflight_latency_ms" in report["metrics"]


def test_reliability_check_backup_and_maintenance(
    tmp_path: Path,
) -> None:
    connection = connect(tmp_path / "memorist.sqlite")
    apply_migrations(connection)
    check = run_consistency_check(connection)
    assert check["status"] in {"ok", "issues_found"}
    backup = backup_sqlite(connection, tmp_path / "backup.sqlite")
    assert backup["status"] == "ok"
    assert (tmp_path / "backup.sqlite").exists()
    checkpoint = wal_checkpoint(connection)
    assert checkpoint["status"] == "ok"
    secure = secure_delete_check(connection)
    assert "tradeoff" in secure
    connection.close()
