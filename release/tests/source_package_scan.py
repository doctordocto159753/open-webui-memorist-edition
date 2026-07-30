from __future__ import annotations

import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from release.scan_source_tree import scan_path  # noqa: E402
from release.source_package import build_source_package  # noqa: E402

REQUIRED_WP02_PATHS = {
    "docs/reference/semantic-candidate-authority.md",
    "docs/reference/wp02-golden-corpus.md",
    "docs/reference/semantic-analysis-contract.md",
    "docs/reference/memory-worker-prompts.md",
    "docs/reference/runtime-role-contracts.md",
    "memorist-core/migrations/0037_semantic_coverage_audit.sql",
    "memorist-core/src/memcore/storage/postgres/migrations/0024_semantic_coverage_audit.sql",
    "memorist-core/src/memcore/memory_worker/prompts/system/jakobson_sentence_analysis_v3.md",
    "memorist-core/src/memcore/memory_worker/prompts/system/semantic_candidate_analysis_v1.md",
}


def run() -> dict[str, Any]:
    try:
        result = build_source_package(
            ROOT / "release" / "source" / "open-webui-memorist-edition-source.zip"
        )
        issues = scan_path(result["zip"])
        with zipfile.ZipFile(result["zip"]) as package:
            names = set(package.namelist())
        missing = sorted(REQUIRED_WP02_PATHS - names)
        with tempfile.TemporaryDirectory(prefix="memorist-source-repro-") as directory:
            repeated = build_source_package(Path(directory) / "source-repeat.zip")
        reproducible = result["digest"] == repeated["digest"]
        return {
            "name": "source_package_scan",
            "passed": not issues and not missing and reproducible,
            "classification": "real",
            "failing_step": (
                None
                if not issues and not missing and reproducible
                else "source_package_scan"
            ),
            "remediation_hint": (
                None
                if not issues and not missing and reproducible
                else "Run `python release/source_package.py`."
            ),
            "package": result,
            "issues": [issue.__dict__ for issue in issues],
            "missing_required_wp02_paths": missing,
            "repeat_digest": repeated["digest"],
            "reproducible": reproducible,
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
