from __future__ import annotations

import sqlite3
from typing import Any

from memcore.models import new_uuid, utc_now
from memcore.validators.ijson import dump_ijson


def persist_attachment_safety_report(
    connection: sqlite3.Connection,
    report: dict[str, Any],
    attachment_uuid: str | None = None,
) -> str:
    report_uuid = new_uuid()
    with connection:
        connection.execute(
            """
            INSERT INTO security_attachment_reports (
                security_report_uuid, attachment_uuid, instruction_like_content_detected,
                untrusted_memory_count, trusted_directive_count, escaped_delimiter_count,
                sensitive_item_excluded_count, security_warnings_ijson, created_at, schema_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            """,
            (
                report_uuid,
                attachment_uuid,
                int(bool(report.get("instruction_like_content_detected"))),
                int(report.get("untrusted_memory_count", 0)),
                int(report.get("trusted_directive_count", 0)),
                int(report.get("escaped_delimiter_count", 0)),
                int(report.get("sensitive_item_excluded_count", 0)),
                dump_ijson({"warnings": report.get("security_warnings", [])}),
                utc_now(),
            ),
        )
    return report_uuid
