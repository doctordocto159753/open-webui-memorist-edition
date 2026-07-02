from __future__ import annotations

from pathlib import Path
from typing import Any

from memcore.validators.ijson import dump_ijson


def write_performance_report(output_dir: str | Path, report: dict[str, Any]) -> dict[str, str]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    ijson = root / "perf-report.ijson"
    md = root / "perf-report.md"
    ijson.write_text(dump_ijson(report), encoding="utf-8")
    md.write_text("# Memorist Performance Report\n\n" + dump_ijson(report) + "\n", encoding="utf-8")
    return {"ijson": str(ijson), "markdown": str(md)}
