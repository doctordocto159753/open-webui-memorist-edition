from __future__ import annotations

import importlib
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "release" / "artifacts"
CORE_SRC = ROOT / "memorist-core" / "src"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(CORE_SRC) not in sys.path:
    sys.path.insert(0, str(CORE_SRC))

FULL_GATES = [
    "full_postgres_canonical_smoke",
    "full_postgres_job_concurrency",
    "full_scheduler_live_chat_preemption",
    "full_import_live_chat_smoke",
    "full_falkordb_projection_smoke",
    "full_falkordb_rebuild_smoke",
    "full_graph_retrieval_smoke",
    "full_graph_down_fallback",
    "full_graph_forget_residue_smoke",
    "full_sqlite_to_postgres_migration_smoke",
    "full_compose_smoke",
]

CRITICAL_GATES = {
    "full_postgres_canonical_smoke": "PostgreSQL canonical smoke failed.",
    "full_falkordb_projection_smoke": "FalkorDB projection smoke failed.",
    "full_graph_forget_residue_smoke": "Graph forget/residue smoke failed.",
}


def main() -> int:
    started = time.perf_counter()
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    results = [_run_gate(name) for name in FULL_GATES]
    report = _build_report(results, started)
    _write_reports(report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    failures = [item for item in results if item["status"] == "failed"]
    return 1 if failures else 0


def _run_gate(name: str) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        module = importlib.import_module(f"release.tests.{name}")
        result = module.run()
        if "status" not in result:
            result["status"] = "passed" if result.get("passed") else "failed"
        result["duration_ms"] = int((time.perf_counter() - started) * 1000)
        result["required_for_full_beta"] = True
        result["manual_only"] = result["status"] == "manual-only"
        return result
    except Exception as error:
        return {
            "name": name,
            "status": "failed",
            "passed": False,
            "skipped": False,
            "blocks_full_certification": True,
            "required_for_full_beta": True,
            "duration_ms": int((time.perf_counter() - started) * 1000),
            "failing_step": "full_mode_check_import_or_run",
            "remediation_hint": str(error)[:240],
        }


def _build_report(results: list[dict[str, Any]], started: float) -> dict[str, Any]:
    failed = [item for item in results if item["status"] == "failed"]
    skipped = [item for item in results if item["status"] in {"skipped", "manual-only"}]
    passed = [item for item in results if item["status"] == "passed"]
    critical_failures = [item for item in failed if item["name"] in CRITICAL_GATES]
    all_required_passed = len(passed) == len(results) and not failed and not skipped
    if critical_failures:
        recommendation = "Full Mode NO-GO"
        full_mode_status = "no-go"
    elif all_required_passed:
        recommendation = "Full Mode beta-supported"
        full_mode_status = "beta-supported"
    else:
        recommendation = "Full Mode experimental preview, materially improved"
        full_mode_status = "experimental-preview"
    return {
        "created_at": now_z(),
        "duration_ms": int((time.perf_counter() - started) * 1000),
        "full_mode_status": full_mode_status,
        "certification_recommendation": recommendation,
        "required_wording": (
            "Full Mode: certified in local Docker test environment."
            if all_required_passed
            else "Full Mode: experimental preview; external certification incomplete."
        ),
        "summary": {
            "passed": len(passed),
            "failed": len(failed),
            "skipped_or_manual": len(skipped),
            "all_required_passed": all_required_passed,
            "skipped_manual_blocks_certification": bool(skipped),
            "critical_failures": [
                {
                    "name": item["name"],
                    "reason": CRITICAL_GATES[item["name"]],
                }
                for item in critical_failures
            ],
        },
        "certification_rules": {
            "skipped_or_manual_does_not_count_as_pass": True,
            "postgres_canonical_required": True,
            "falkordb_projection_required": True,
            "full_compose_required": True,
            "graph_forget_residue_required": True,
        },
        "results": results,
    }


def _write_reports(report: dict[str, Any]) -> None:
    ijson_path = ARTIFACTS / "full-mode-certification-report.ijson"
    md_path = ARTIFACTS / "full-mode-certification-report.md"
    ijson_path.write_text(
        json.dumps(report, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    md_path.write_text(_render_markdown(report), encoding="utf-8")


def _render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Full Mode Certification Report",
        "",
        f"- Created: `{report['created_at']}`",
        f"- Full Mode status: `{report['full_mode_status']}`",
        f"- Recommendation: `{report['certification_recommendation']}`",
        f"- Required wording: {report['required_wording']}",
        "",
        "Skipped or manual-only gates do not count as passed.",
        "",
        "| Gate | Status | Blocks Full Certification | Duration ms |",
        "| --- | --- | --- | ---: |",
    ]
    for item in report["results"]:
        lines.append(
            "| `{name}` | `{status}` | `{blocks}` | {duration} |".format(
                name=item["name"],
                status=item["status"],
                blocks=item.get("blocks_full_certification", True),
                duration=item.get("duration_ms", 0),
            )
        )
    lines.extend(["", "## Skips And Failures"])
    for item in report["results"]:
        if item["status"] not in {"passed"}:
            reason = item.get("skip_reason") or item.get("failing_step") or "not passed"
            hint = item.get("remediation_hint") or item.get("docker_reason") or ""
            lines.append(f"- `{item['name']}`: {reason}. {hint}".rstrip())
    return "\n".join(lines) + "\n"


def now_z() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


if __name__ == "__main__":
    raise SystemExit(main())
