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

from memcore.imports.repositories import ImportRepository  # noqa: E402
from memcore.reliability.backup import backup_sqlite  # noqa: E402
from memcore.reliability.recovery import recover_interrupted_operations  # noqa: E402
from memcore.repositories.domain import JobRepository  # noqa: E402
from memcore.storage.migrations import apply_migrations  # noqa: E402
from memcore.storage.sqlite import connect  # noqa: E402


def run() -> dict[str, Any]:
    exit_code = main()
    return {
        "name": "recovery_tests",
        "passed": exit_code == 0,
        "classification": "real",
        "failing_step": None if exit_code == 0 else "recovery_tests",
        "remediation_hint": None if exit_code == 0 else "Run `make recovery-tests`.",
    }


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="memorist-recovery-") as temp_dir:
        base = Path(temp_dir)
        db_path = base / "memorist.sqlite"
        backup_path = base / "backup.sqlite"
        connection = connect(db_path)
        try:
            apply_migrations(connection)
            seeded = _seed_interrupted(connection)
            backup = backup_sqlite(connection, backup_path)
            planned = recover_interrupted_operations(connection, apply=False)
            applied = recover_interrupted_operations(connection, apply=True)
            after = recover_interrupted_operations(connection, apply=False)
            result = {
                "status": "ok"
                if backup["status"] == "ok"
                and planned["action_count"] >= 3
                and applied["action_count"] >= 3
                and after["action_count"] == 0
                else "failed",
                "seeded": seeded,
                "backup": backup,
                "planned": planned,
                "applied": applied,
                "after": after,
                "backup_exists": backup_path.exists(),
            }
            print(json.dumps(result, indent=2))
            return 0 if result["status"] == "ok" else 1
        finally:
            connection.close()


def _seed_interrupted(connection: Any) -> dict[str, str]:
    import_run = ImportRepository(connection).create_run("0" * 64, "inspect", {})
    ImportRepository(connection).update_run(import_run["import_run_uuid"], {"status": "committing"})
    job = JobRepository(connection).enqueue_job_once(
        "recovery-smoke",
        {"purpose": "p2"},
        priority=1,
    )
    with connection:
        connection.execute("UPDATE jobs SET status = 'running' WHERE job_uuid = ?", (job.job_uuid,))
        connection.execute(
            """
            INSERT INTO privacy_requests (
                privacy_request_uuid, request_type, target_type, target_uuid,
                requested_scope_ijson, actor_type, actor_uuid, status,
                confirmation_token_hash, confirmation_expires_at,
                policy_decision_ijson, created_at, confirmed_at, completed_at, schema_version
            )
            VALUES (
                'privacy-recovery-smoke', 'forget', 'message', 'message-recovery-smoke',
                '{}', 'user', NULL, 'executing',
                'hash', '2099-01-01T00:00:00Z',
                '{}', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, NULL, 1
            )
            """
        )
    return {
        "import_run_uuid": import_run["import_run_uuid"],
        "job_uuid": job.job_uuid,
        "privacy_request_uuid": "privacy-recovery-smoke",
    }


if __name__ == "__main__":
    raise SystemExit(main())
