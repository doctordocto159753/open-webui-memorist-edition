from __future__ import annotations

from pathlib import Path
from typing import Any

from memcore.validators.ijson import dump_ijson


def write_consistency_report(report: dict[str, Any], output_json: str | Path) -> dict[str, str]:
    json_path = Path(output_json)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    md_path = json_path.with_suffix(".md")
    json_path.write_text(dump_ijson(report), encoding="utf-8")
    md_path.write_text(_markdown_report(report), encoding="utf-8")
    return {"json": str(json_path), "markdown": str(md_path)}


def _markdown_report(report: dict[str, Any]) -> str:
    lines = [
        "# Memorist Consistency Report",
        "",
        f"- Status: `{report['status']}`",
        f"- Checked at: `{report['checked_at']}`",
        f"- Issue count: `{report['issue_count']}`",
        "",
        "## Issues",
        "",
    ]
    if not report["issues"]:
        lines.append("No consistency issues were found.")
    else:
        for issue in report["issues"]:
            lines.append(
                "- "
                f"`{issue['severity']}` "
                f"`{issue['check']}` "
                f"`{issue.get('id', '-')}`"
            )
    lines.append("")
    return "\n".join(lines)

