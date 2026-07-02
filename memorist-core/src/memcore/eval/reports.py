from __future__ import annotations

from pathlib import Path
from typing import Any

from memcore.validators.ijson import dump_ijson


def write_reports(output_dir: str | Path, run_id: str, report: dict[str, Any]) -> dict[str, str]:
    resolved = Path(output_dir)
    resolved.mkdir(parents=True, exist_ok=True)
    ijson_path = resolved / "eval-report.ijson"
    markdown_path = resolved / "eval-report.md"
    ijson_path.write_text(dump_ijson(report), encoding="utf-8")
    markdown_path.write_text(_markdown(run_id, report), encoding="utf-8")
    return {"ijson": str(ijson_path), "markdown": str(markdown_path)}


def _markdown(run_id: str, report: dict[str, Any]) -> str:
    lines = ["# Memorist Eval Report", "", f"Run: `{run_id}`", "", "## Summary", ""]
    for key, value in report["metrics"].items():
        lines.append(f"- `{key}`: {value}")
    lines.extend(["", "## Cases", ""])
    for case in report["cases"]:
        status = "PASS" if case["passed"] else "FAIL"
        lines.append(f"- {status} `{case['case_id']}` — {case['category']}")
    return "\n".join(lines) + "\n"
