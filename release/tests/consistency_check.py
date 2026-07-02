from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
CORE_SRC = ROOT / "memorist-core" / "src"
if str(CORE_SRC) not in sys.path:
    sys.path.insert(0, str(CORE_SRC))

from memcore.models import CreatorType, MessageRole  # noqa: E402
from memcore.reliability.consistency import run_consistency_check  # noqa: E402
from memcore.reliability.consistency.report import write_consistency_report  # noqa: E402
from memcore.repositories import (  # noqa: E402
    MessageRepository,
    SessionRepository,
    WorkspaceRepository,
)
from memcore.storage.direct_write_audit import audit_direct_writes  # noqa: E402
from memcore.storage.migrations import apply_migrations  # noqa: E402
from memcore.storage.sqlite import connect  # noqa: E402


def run() -> dict[str, Any]:
    exit_code = main()
    return {
        "name": "consistency_check",
        "passed": exit_code == 0,
        "classification": "real",
        "failing_step": None if exit_code == 0 else "consistency_check",
        "remediation_hint": None if exit_code == 0 else "Run `make consistency-check`.",
    }


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="memorist-consistency-") as temp_dir:
        base = Path(temp_dir)
        db_path = base / "memorist.sqlite"
        report_path = base / "consistency.ijson"
        connection = connect(db_path)
        try:
            apply_migrations(connection)
            _seed(connection)
            report = run_consistency_check(connection)
            paths = write_consistency_report(report, report_path)
            audit = audit_direct_writes(ROOT / "memorist-core" / "src")
            result = {
                "status": "ok"
                if report["status"] == "ok" and audit["status"] == "ok"
                else "failed",
                "consistency": report,
                "report_paths": paths,
                "direct_write_audit": {
                    "status": audit["status"],
                    "unexpected_count": audit["unexpected_count"],
                },
            }
            print(json.dumps(result, indent=2))
            return 0 if result["status"] == "ok" else 1
        finally:
            connection.close()


def _seed(connection: Any) -> None:
    workspace = WorkspaceRepository(connection).create_workspace("Consistency")
    session = SessionRepository(connection).create_session(
        workspace_uuid=workspace.workspace_uuid,
        title="Consistency session",
    )
    MessageRepository(connection).create_message(
        session.session_uuid,
        MessageRole.USER,
        CreatorType.USER,
        raw_text="consistency checker smoke",
    )


if __name__ == "__main__":
    raise SystemExit(main())
