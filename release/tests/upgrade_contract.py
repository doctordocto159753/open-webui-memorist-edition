"""Upgrade-compatibility contract for the one-click package.

The corrected PR5-G package must be able to adopt an existing PR #45
(0.2.0-beta.1) installation without data loss: the Compose project name, every
named data volume, and the .env keys the previous installer wrote must keep
their exact identities. This gate freezes that contract so a future edit that
would silently orphan user data fails CI instead.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "release" / "memorist-openwebui"
CORE = ROOT / "memorist-core"

REQUIRED_VOLUMES = [
    "openwebui-data",
    "memorist-data",
    "memorist-objects",
    "memorist-import-staging",
    "memorist-exports",
    "memorist-postgres-data",
    "falkordb-data",
]

# .env keys written by the beta.1 installer that the new package must keep
# honoring unchanged (an existing .env remains reusable as-is).
# MEMORIST_MODE is consumed by the lifecycle scripts, checked separately.
REQUIRED_ENV_KEYS = [
    "WEBUI_SECRET_KEY",
    "MEMORIST_ACTOR_ASSERTION_SECRET",
    "MEMORIST_ACTOR_SERVICE_TOKEN",
    "MEMORIST_OPENWEBUI_WORKSPACE_UUID",
    "MEMORIST_POSTGRES_PASSWORD",
    "MEMORIST_POSTGRES_DSN",
    "OPEN_WEBUI_PORT",
    "MEMORIST_PORT",
]


def run() -> dict[str, object]:
    issues: list[str] = []

    compose = (PACKAGE / "compose.yml").read_text(encoding="utf-8")
    if "name: ${COMPOSE_PROJECT_NAME:-memorist}" not in compose:
        issues.append("compose project name default changed from 'memorist'")
    volumes_section = compose.split("\nvolumes:\n", 1)
    declared = re.findall(r"^  ([a-z\-]+):\s*$", volumes_section[1], re.M) if len(volumes_section) == 2 else []
    for volume in REQUIRED_VOLUMES:
        if volume not in declared:
            issues.append(f"named volume '{volume}' missing from compose.yml")

    combined = compose + "".join(
        (PACKAGE / name).read_text(encoding="utf-8")
        for name in ("compose.lite.yml", "compose.full.yml")
    )
    for key in REQUIRED_ENV_KEYS:
        if f"${{{key}" not in combined:
            issues.append(f".env key '{key}' is no longer consumed by the compose files")

    common = (PACKAGE / "scripts" / "MemoristCommon.psm1").read_text(encoding="utf-8")
    if "MEMORIST_MODE" not in common:
        issues.append("MEMORIST_MODE is no longer the installed-mode authority in the scripts")

    sqlite_wp02 = (CORE / "migrations" / "0037_semantic_coverage_audit.sql")
    postgres_wp02 = (
        CORE
        / "src"
        / "memcore"
        / "storage"
        / "postgres"
        / "migrations"
        / "0024_semantic_coverage_audit.sql"
    )
    for migration in (sqlite_wp02, postgres_wp02):
        if not migration.is_file():
            issues.append(f"WP02 migration missing: {migration.relative_to(ROOT)}")
            continue
        sql = migration.read_text(encoding="utf-8")
        for table in (
            "semantic_coverage_runs",
            "semantic_coverage_items",
            "semantic_candidate_links",
        ):
            if table not in sql:
                issues.append(f"{migration.name} omits {table}")

    # The one-click contract: entry points at the extracted root.
    for entry in (
        "Memorist.cmd",
        "Install-Memorist.ps1",
        "Start-Memorist.ps1",
        "Stop-Memorist.ps1",
        "Restart-Memorist.ps1",
        "Show-Memorist-Logs.ps1",
        "Reset-Memorist-Data.ps1",
        "Uninstall-Memorist.ps1",
        "Test-Memorist-Full.ps1",
    ):
        if not (PACKAGE / entry).is_file():
            issues.append(f"entry point '{entry}' missing from the package source")

    return {
        "name": "upgrade_contract",
        "passed": not issues,
        "classification": "real",
        "failing_step": None if not issues else "upgrade_contract",
        "issues": issues,
    }


def main() -> int:
    result = run()
    print(json.dumps(result, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
