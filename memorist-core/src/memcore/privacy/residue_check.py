from __future__ import annotations

import sqlite3
from typing import Any

AUDIT_TABLES = {
    "privacy_requests",
    "privacy_request_items",
    "erasure_receipts",
    "semantic_coverage_runs",
    "semantic_coverage_items",
    "semantic_candidate_links",
}
TEXT_COLUMNS = {
    "messages": ("raw_text", "raw_payload_ijson", "snapshot_ijson"),
    "message_versions": ("raw_text", "raw_payload_ijson", "snapshot_ijson"),
    "memory_processing_runs": ("raw_output_ijson", "validation_errors_ijson", "error_text"),
    "text_units": ("text",),
    "memory_candidates": (
        "object_ijson",
        "normalized_text",
        "extraction_metadata_ijson",
        "rejection_reason_codes_ijson",
    ),
    "candidate_evidence": ("evidence_text",),
    "jakobson_analysis_runs": ("warnings_ijson", "raw_output_ijson"),
    "jakobson_sentence_annotations": (
        "sentence_text",
        "sender_evidence",
        "receiver_evidence",
        "message_evidence",
        "context_evidence",
        "code_evidence",
        "contact_channel_evidence",
        "raw_sentence_output_ijson",
    ),
    "memory_signal_routes": ("reason",),
    "memory_versions": ("normalized_text", "value_ijson", "change_reason_ijson"),
    "session_events": ("payload_ijson",),
    "memory_context_attachments": (
        "ijson_attachment",
        "rendered_attachment",
        "source_block_uuids_ijson",
        "source_memory_uuids_ijson",
        "source_fact_edge_uuids_ijson",
    ),
    "memory_blocks": (
        "description",
        "value",
        "source_memory_uuids_ijson",
        "source_nucleus_uuids_ijson",
        "source_fact_edge_uuids_ijson",
        "builder_policy_ijson",
    ),
    "memory_block_versions": ("value", "structured_value_ijson", "change_reason"),
    "session_hot_cache": (
        "current_problem",
        "last_user_intent",
        "active_constraints_ijson",
        "unresolved_questions_ijson",
        "recent_entities_ijson",
        "recent_memory_uuids_ijson",
        "current_plan",
    ),
    "import_records": ("raw_payload_ijson", "normalized_payload_ijson"),
    "import_mappings": ("source_object_id", "source_fingerprint", "target_uuid"),
}


def run_forget_residue_check(
    connection: sqlite3.Connection,
    target_uuid: str,
    forbidden_text: str | None = None,
    *,
    check_uuid_references: bool = True,
) -> dict[str, Any]:
    residue = _uuid_residue(connection, target_uuid) if check_uuid_references else []
    if forbidden_text:
        residue.extend(_text_residue(connection, forbidden_text))
    return {
        "status": "clean" if not residue else "residue_found",
        "target_uuid": target_uuid,
        "residue_count": len(residue),
        "residue": residue,
        "raw_content_returned": False,
    }


def _uuid_residue(connection: sqlite3.Connection, target_uuid: str) -> list[dict[str, Any]]:
    residue: list[dict[str, Any]] = []
    for table in _tables(connection):
        for column, is_primary_key in _columns_with_pk(connection, table):
            if is_primary_key:
                continue
            if not column.endswith("_uuid") and column != "target_uuid":
                continue
            if table in AUDIT_TABLES:
                continue
            rows = connection.execute(
                f"SELECT rowid AS row_id FROM {table} WHERE {column} = ? LIMIT 20",
                (target_uuid,),
            ).fetchall()
            for row in rows:
                residue.append(
                    {
                        "type": "uuid_reference",
                        "table": table,
                        "column": column,
                        "row_id": row["row_id"],
                    }
                )
    return residue


def _text_residue(connection: sqlite3.Connection, forbidden_text: str) -> list[dict[str, Any]]:
    residue: list[dict[str, Any]] = []
    like_value = f"%{forbidden_text}%"
    for table, candidate_columns in TEXT_COLUMNS.items():
        if not _table_exists(connection, table):
            continue
        columns = set(_columns(connection, table))
        for column in candidate_columns:
            if column not in columns:
                continue
            rows = connection.execute(
                f"SELECT rowid AS row_id FROM {table} WHERE {column} LIKE ? LIMIT 20",
                (like_value,),
            ).fetchall()
            for row in rows:
                residue.append(
                    {
                        "type": "text_match",
                        "table": table,
                        "column": column,
                        "row_id": row["row_id"],
                    }
                )
    return residue


def _tables(connection: sqlite3.Connection) -> list[str]:
    return [
        row["name"]
        for row in connection.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type IN ('table', 'view')
              AND name NOT LIKE 'sqlite_%'
            """
        )
    ]


def _columns(connection: sqlite3.Connection, table: str) -> list[str]:
    return [row["name"] for row in connection.execute(f"PRAGMA table_info({table})")]


def _columns_with_pk(connection: sqlite3.Connection, table: str) -> list[tuple[str, bool]]:
    return [
        (row["name"], bool(row["pk"])) for row in connection.execute(f"PRAGMA table_info({table})")
    ]


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    row = connection.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?",
        (table,),
    ).fetchone()
    return row is not None
