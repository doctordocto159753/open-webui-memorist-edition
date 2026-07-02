from __future__ import annotations

import sqlite3
from typing import Any

from memcore.models import utc_now
from memcore.reliability.consistency.checks import (
    fts_issues,
    import_mapping_issues,
    sqlite_integrity_issues,
    stale_job_issues,
    structural_issues,
)
from memcore.version import SCHEMA_VERSION


def run_consistency_check(connection: sqlite3.Connection) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    issues.extend(sqlite_integrity_issues(connection))
    issues.extend(structural_issues(connection))
    issues.extend(import_mapping_issues(connection))
    issues.extend(fts_issues(connection))
    issues.extend(stale_job_issues(connection))
    severity_counts = {
        "error": sum(1 for issue in issues if issue["severity"] == "error"),
        "warning": sum(1 for issue in issues if issue["severity"] == "warning"),
    }
    return {
        "status": "ok" if severity_counts["error"] == 0 else "issues_found",
        "checked_at": utc_now(),
        "schema_version": SCHEMA_VERSION,
        "issue_count": len(issues),
        "severity_counts": severity_counts,
        "issues": issues,
    }

