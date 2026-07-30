from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def run() -> dict[str, object]:
    issues: list[str] = []
    product = (ROOT / ".github" / "scripts" / "ci-product-e2e.sh").read_text(
        encoding="utf-8"
    )
    start = (
        ROOT / "release" / "memorist-openwebui" / "Start-Memorist.ps1"
    ).read_text(encoding="utf-8")

    if product.count("docker build \\") != 1:
        issues.append("Product E2E must invoke exactly one explicit derivative image build")
    start_calls = re.findall(r"\./Start-Memorist\.ps1[^\n]*", product)
    if len(start_calls) != 1 or "-NoBuild" not in start_calls[0]:
        issues.append("Product E2E must start the package exactly once with -NoBuild")
    restart_tail = product.split("# Restart the existing containers in place.", 1)
    if len(restart_tail) != 2 or '"${compose[@]}" restart' not in restart_tail[1]:
        issues.append("persistence phase must restart the existing Compose containers")
    if len(restart_tail) == 2 and (
        "Stop-Memorist.ps1" in restart_tail[1]
        or "Start-Memorist.ps1" in restart_tail[1]
        or " compose up " in restart_tail[1]
    ):
        issues.append("persistence phase must not stop, rebuild, or redeploy the stack")
    if "[switch]$NoBuild" not in start:
        issues.append("packaged start entrypoint does not expose explicit -NoBuild")
    if "if (-not $NoBuild) { $startArguments += '--build' }" not in start:
        issues.append("local default build and CI no-build behavior are not explicitly separated")

    return {
        "name": "ci_single_deployment_contract",
        "passed": not issues,
        "classification": "real",
        "failing_step": None if not issues else "ci_single_deployment_contract",
        "issues": issues,
    }


def main() -> int:
    result = run()
    print(json.dumps(result, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
