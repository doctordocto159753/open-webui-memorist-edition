from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from release.scan_source_tree import scan_path  # noqa: E402
from release.source_package import build_source_package  # noqa: E402


def run() -> dict[str, Any]:
    try:
        result = build_source_package(
            ROOT / "release" / "source" / "open-webui-memorist-edition-source.zip"
        )
        issues = scan_path(result["zip"])
        return {
            "name": "source_package_scan",
            "passed": not issues,
            "classification": "real",
            "failing_step": None if not issues else "source_package_scan",
            "remediation_hint": None if not issues else "Run `python release/source_package.py`.",
            "package": result,
            "issues": [issue.__dict__ for issue in issues],
        }
    except Exception as error:
        return {
            "name": "source_package_scan",
            "passed": False,
            "classification": "real",
            "failing_step": "exception",
            "remediation_hint": str(error)[:200],
        }


def main() -> int:
    result = run()
    import json

    print(json.dumps(result, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
