from __future__ import annotations

import importlib
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "release" / "artifacts"
CORE_SRC = ROOT / "memorist-core" / "src"
CORE_PROJECT = ROOT / "memorist-core"

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
    managed_exit = _run_in_managed_core_environment()
    if managed_exit is not None:
        return managed_exit
    started = time.perf_counter()
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    results = [_run_gate(name) for name in FULL_GATES]
    report = _build_report(results, started)
    _write_reports(report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["summary"]["all_required_passed"] else 1


def _run_in_managed_core_environment() -> int | None:
    """Re-enter through Core's dependency environment when system Python is bare."""

    if os.getenv("MEMORIST_FULL_MODE_RUNTIME_BOOTSTRAPPED") == "1":
        return None
    try:
        importlib.import_module("psycopg")
        return None
    except ModuleNotFoundError:
        pass

    candidates = [
        CORE_PROJECT / ".venv" / "Scripts" / "python.exe",
        CORE_PROJECT / ".venv" / "bin" / "python",
    ]
    command: list[str] | None = None
    for candidate in candidates:
        if candidate.is_file() and candidate.resolve() != Path(sys.executable).resolve():
            command = [str(candidate), str(Path(__file__).resolve()), *sys.argv[1:]]
            break
    if command is None:
        uv = shutil.which("uv")
        if uv is not None:
            command = [
                uv,
                "run",
                "--project",
                str(CORE_PROJECT),
                "python",
                str(Path(__file__).resolve()),
                *sys.argv[1:],
            ]
    if command is None:
        return None
    environment = dict(os.environ)
    environment["MEMORIST_FULL_MODE_RUNTIME_BOOTSTRAPPED"] = "1"
    return subprocess.run(command, cwd=ROOT, env=environment, check=False).returncode


def _run_gate(name: str) -> dict[str, Any]:
    started = time.perf_counter()
    started_at = now_z()
    try:
        module = importlib.import_module(f"release.tests.{name}")
        result = cast(dict[str, Any], module.run())
        if "status" not in result:
            result["status"] = "passed" if result.get("passed") else "failed"
        result["duration_ms"] = int((time.perf_counter() - started) * 1000)
        result["started_at"] = started_at
        result["finished_at"] = now_z()
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
            "started_at": started_at,
            "finished_at": now_z(),
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
        "commit_sha": _command_output(["git", "rev-parse", "HEAD"]),
        "platform": platform.platform(),
        "python_version": platform.python_version(),
        "service_versions": {
            "postgres": "postgres:16.9-alpine3.22",
            "falkordb": (
                "falkordb/falkordb@sha256:"
                "2496643cabd67e87fd82458383400c049324daec1fe674ba0db4c5bdaca5d25f"
            ),
            "open_webui": "ghcr.io/open-webui/open-webui:v0.9.6",
            "docker": _command_output(["docker", "version", "--format", "{{.Server.Version}}"]),
        },
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
        f"- Commit: `{report['commit_sha']}`",
        f"- Platform: `{report['platform']}`",
        f"- Service versions: `{json.dumps(report['service_versions'], sort_keys=True)}`",
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


def _command_output(command: list[str]) -> str:
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        return completed.stdout.strip()
    except Exception:
        return "unavailable"


if __name__ == "__main__":
    raise SystemExit(main())
