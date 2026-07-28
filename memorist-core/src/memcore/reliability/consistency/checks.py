from __future__ import annotations

import sqlite3
from typing import Any

CHECKS: tuple[tuple[str, str], ...] = (
    (
        "messages_reference_sessions",
        """
        SELECT message_uuid AS id
        FROM messages
        WHERE session_uuid NOT IN (SELECT session_uuid FROM sessions)
        """,
    ),
    (
        "message_versions_reference_messages",
        """
        SELECT message_version_uuid AS id
        FROM message_versions
        WHERE message_uuid NOT IN (SELECT message_uuid FROM messages)
        """,
    ),
    (
        "memories_reference_versions",
        """
        SELECT memory_uuid AS id
        FROM memories
        WHERE current_version_uuid IS NOT NULL
          AND current_version_uuid NOT IN (
            SELECT memory_version_uuid FROM memory_versions
          )
        """,
    ),
    (
        "memory_evidence_links_reference_text_units",
        """
        SELECT mel.memory_evidence_link_uuid AS id
        FROM memory_evidence_links
        AS mel
        WHERE mel.evidence_uuid NOT IN (SELECT evidence_uuid FROM candidate_evidence)
        """,
    ),
    (
        "retrieval_candidates_reference_versions",
        """
        SELECT retrieval_candidate_uuid AS id
        FROM retrieval_candidates
        WHERE memory_version_uuid IS NOT NULL
          AND memory_version_uuid NOT IN (SELECT memory_version_uuid FROM memory_versions)
        """,
    ),
    (
        "block_versions_reference_blocks",
        """
        SELECT block_version_uuid AS id
        FROM memory_block_versions
        WHERE block_uuid NOT IN (SELECT block_uuid FROM memory_blocks)
        """,
    ),
    (
        "privacy_items_reference_requests",
        """
        SELECT privacy_request_item_uuid AS id
        FROM privacy_request_items
        WHERE privacy_request_uuid NOT IN (SELECT privacy_request_uuid FROM privacy_requests)
        """,
    ),
    (
        "semantic_coverage_items_reference_runs",
        """
        SELECT coverage_item_uuid AS id
        FROM semantic_coverage_items
        WHERE coverage_run_uuid NOT IN (
            SELECT coverage_run_uuid FROM semantic_coverage_runs
        )
        """,
    ),
    (
        "semantic_candidate_links_are_consistent",
        """
        SELECT proposal_uuid AS id
        FROM semantic_candidate_links
        WHERE coverage_item_uuid NOT IN (
                SELECT coverage_item_uuid FROM semantic_coverage_items
              )
           OR (state = 'candidate_linked' AND (
                candidate_uuid IS NULL
                OR candidate_uuid <> proposal_uuid
                OR candidate_uuid NOT IN (
                    SELECT candidate_uuid FROM memory_candidates
                )
              ))
        """,
    ),
)


def structural_issues(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for check_name, query in CHECKS:
        try:
            for row in connection.execute(query):
                issues.append({"check": check_name, "id": row["id"], "severity": "error"})
        except sqlite3.OperationalError as exc:
            issues.append(
                {"check": check_name, "id": "schema", "severity": "warning", "message": str(exc)}
            )
    return issues


def import_mapping_issues(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    if not table_exists(connection, "import_mappings"):
        return []
    rows = connection.execute(
        """
        SELECT source_platform,
               source_object_type,
               COALESCE(source_object_id, source_fingerprint) AS source_key,
               COUNT(*) AS count
        FROM import_mappings
        GROUP BY source_platform, source_object_type, source_key
        HAVING COUNT(*) > 1
        """
    ).fetchall()
    return [
        {
            "check": "import_mappings_unique",
            "id": row["source_key"],
            "severity": "error",
            "count": row["count"],
        }
        for row in rows
    ]


def fts_issues(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    if table_exists(connection, "memory_version_fts") and table_exists(
        connection, "memory_versions"
    ):
        fts_count = int(connection.execute("SELECT COUNT(*) FROM memory_version_fts").fetchone()[0])
        version_count = int(
            connection.execute("SELECT COUNT(*) FROM memory_versions").fetchone()[0]
        )
        if fts_count not in {0, version_count}:
            issues.append(
                {
                    "check": "memory_version_fts_count",
                    "id": "memory_version_fts",
                    "severity": "warning",
                    "fts_count": fts_count,
                    "memory_version_count": version_count,
                }
            )
    return issues


def stale_job_issues(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    if not table_exists(connection, "jobs"):
        return []
    rows = connection.execute(
        """
        SELECT job_uuid AS id, status, updated_at
        FROM jobs
        WHERE status IN ('running', 'claimed')
        """
    ).fetchall()
    return [
        {
            "check": "stale_running_jobs",
            "id": row["id"],
            "severity": "warning",
            "status": row["status"],
            "updated_at": row["updated_at"],
        }
        for row in rows
    ]


def sqlite_integrity_issues(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    quick_check = connection.execute("PRAGMA quick_check").fetchone()[0]
    if quick_check != "ok":
        issues.append(
            {
                "check": "sqlite_quick_check",
                "id": "database",
                "severity": "error",
                "message": quick_check,
            }
        )
    for row in connection.execute("PRAGMA foreign_key_check"):
        issues.append(
            {
                "check": "sqlite_foreign_key_check",
                "id": f"{row['table']}:{row['rowid']}",
                "severity": "error",
                "table": row["table"],
                "parent": row["parent"],
                "fkid": row["fkid"],
            }
        )
    return issues


def table_exists(connection: sqlite3.Connection, table: str) -> bool:
    row = connection.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?",
        (table,),
    ).fetchone()
    return row is not None
