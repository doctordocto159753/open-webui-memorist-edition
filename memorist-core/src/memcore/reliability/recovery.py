from __future__ import annotations

import sqlite3
from typing import Any

from memcore.models import utc_now
from memcore.reliability.consistency.checks import table_exists


def recover_interrupted_operations(
    connection: sqlite3.Connection,
    apply: bool = False,
) -> dict[str, Any]:
    actions: list[dict[str, Any]] = []
    if table_exists(connection, "import_runs"):
        actions.extend(_recover_imports(connection, apply))
    if table_exists(connection, "privacy_requests"):
        actions.extend(_recover_privacy_requests(connection, apply))
    if table_exists(connection, "jobs"):
        actions.extend(_recover_jobs(connection, apply))
    return {
        "status": "applied" if apply else "planned",
        "action_count": len(actions),
        "actions": actions,
        "recovered_at": utc_now(),
    }


def _recover_imports(connection: sqlite3.Connection, apply: bool) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT import_run_uuid, status
        FROM import_runs
        WHERE status IN ('committing', 'running')
        """
    ).fetchall()
    actions = [
        {
            "target_type": "import_run",
            "target_uuid": row["import_run_uuid"],
            "from_status": row["status"],
            "to_status": "paused",
            "reason": "interrupted_import_can_resume",
        }
        for row in rows
    ]
    if apply and actions:
        with connection:
            connection.execute(
                """
                UPDATE import_runs
                SET status = 'paused'
                WHERE status IN ('committing', 'running')
                """
            )
            connection.execute(
                """
                UPDATE import_progress
                SET paused = 1, phase = 'paused', updated_at = ?
                WHERE import_run_uuid IN (
                    SELECT import_run_uuid FROM import_runs WHERE status = 'paused'
                )
                """,
                (utc_now(),),
            )
    return actions


def _recover_privacy_requests(
    connection: sqlite3.Connection,
    apply: bool,
) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT privacy_request_uuid, status
        FROM privacy_requests
        WHERE status IN ('executing', 'confirmed')
        """
    ).fetchall()
    actions = [
        {
            "target_type": "privacy_request",
            "target_uuid": row["privacy_request_uuid"],
            "from_status": row["status"],
            "to_status": "partial_failure",
            "reason": "interrupted_privacy_operation_requires_retry",
        }
        for row in rows
    ]
    if apply and actions:
        with connection:
            connection.execute(
                """
                UPDATE privacy_requests
                SET status = 'partial_failure'
                WHERE status IN ('executing', 'confirmed')
                """
            )
    return actions


def _recover_jobs(connection: sqlite3.Connection, apply: bool) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT job_uuid, status
        FROM jobs
        WHERE status IN ('running', 'claimed')
        """
    ).fetchall()
    actions = [
        {
            "target_type": "job",
            "target_uuid": row["job_uuid"],
            "from_status": row["status"],
            "to_status": "dead",
            "reason": "interrupted_worker_claim",
        }
        for row in rows
    ]
    if apply and actions:
        with connection:
            connection.execute(
                """
                UPDATE jobs
                SET status = 'dead', updated_at = ?
                WHERE status IN ('running', 'claimed')
                """,
                (utc_now(),),
            )
    return actions

