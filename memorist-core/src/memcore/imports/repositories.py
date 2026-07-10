from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from typing import Any

from memcore.imports.models import ImportIssue, ImportRecord, StagedArtifact
from memcore.models import new_uuid, utc_now
from memcore.repositories.domain import RepositoryError
from memcore.repositories.sqlite import SQLiteRepository
from memcore.validators.ijson import canonical_hash_ijson, dump_ijson


class ImportRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self.sqlite = SQLiteRepository(connection)

    def create_run(
        self,
        archive_sha256: str,
        mode: str,
        options: dict[str, Any],
        target_workspace_uuid: str | None = None,
        target_project_uuid: str | None = None,
    ) -> dict[str, Any]:
        run = {
            "import_run_uuid": new_uuid(),
            "source_platform": None,
            "detected_format": None,
            "detected_format_version": None,
            "archive_sha256": archive_sha256,
            "target_workspace_uuid": target_workspace_uuid,
            "target_project_uuid": target_project_uuid,
            "mode": mode,
            "status": "uploaded",
            "options_ijson": dump_ijson(options),
            "schema_fingerprint": None,
            "total_files": 0,
            "total_conversations": 0,
            "total_messages": 0,
            "imported_conversations": 0,
            "imported_messages": 0,
            "skipped_records": 0,
            "warning_count": 0,
            "error_count": 0,
            "report_ijson": None,
            "created_at": utc_now(),
            "completed_at": None,
            "schema_version": 1,
        }
        with self.connection:
            self.sqlite.insert("import_runs", run)
        return run

    def get_run(self, import_run_uuid: str) -> dict[str, Any]:
        row = self.connection.execute(
            "SELECT * FROM import_runs WHERE import_run_uuid = ?",
            (import_run_uuid,),
        ).fetchone()
        if row is None:
            raise RepositoryError(f"Import run not found: {import_run_uuid}")
        return dict(row)

    def update_run(self, import_run_uuid: str, values: Mapping[str, Any]) -> dict[str, Any]:
        with self.connection:
            self.sqlite.update("import_runs", values, "import_run_uuid = ?", (import_run_uuid,))
        return self.get_run(import_run_uuid)

    def add_artifact(self, import_run_uuid: str, artifact: StagedArtifact) -> dict[str, Any]:
        row = {
            "import_artifact_uuid": new_uuid(),
            "import_run_uuid": import_run_uuid,
            "relative_path": artifact.relative_path,
            "artifact_role": artifact.artifact_role,
            "detected_media_type": artifact.detected_media_type,
            "size_bytes": artifact.size_bytes,
            "sha256": artifact.sha256,
            "object_store_path": artifact.object_store_path,
            "parse_status": "pending",
            "security_status": "passed",
            "created_at": utc_now(),
            "schema_version": 1,
        }
        with self.connection:
            self.sqlite.insert("import_artifacts", row)
        return row

    def list_artifacts(self, import_run_uuid: str) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in self.connection.execute(
                "SELECT * FROM import_artifacts WHERE import_run_uuid = ? ORDER BY relative_path",
                (import_run_uuid,),
            )
        ]

    def add_record(
        self,
        import_run_uuid: str,
        record: ImportRecord,
        record_index: int,
        import_artifact_uuid: str | None = None,
    ) -> dict[str, Any]:
        raw_payload = strip_none(record.raw_payload)
        normalized_payload = strip_none(record.normalized_payload)
        fingerprint = canonical_hash_ijson(normalized_payload)
        row = {
            "import_record_uuid": new_uuid(),
            "import_run_uuid": import_run_uuid,
            "import_artifact_uuid": import_artifact_uuid,
            "source_record_type": record.source_record_type,
            "source_record_id": record.source_record_id,
            "source_parent_id": record.source_parent_id,
            "record_index": record_index,
            "raw_payload_ijson": dump_ijson(raw_payload),
            "normalized_payload_ijson": dump_ijson(normalized_payload),
            "content_fingerprint": fingerprint,
            "reconstruction_confidence": record.reconstruction_confidence,
            "status": "staged",
            "created_at": utc_now(),
            "schema_version": 1,
        }
        with self.connection:
            self.sqlite.insert("import_records", row)
        return row

    def list_records(self, import_run_uuid: str) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in self.connection.execute(
                "SELECT * FROM import_records WHERE import_run_uuid = ? ORDER BY record_index",
                (import_run_uuid,),
            )
        ]

    def add_issue(self, import_run_uuid: str, issue: ImportIssue) -> dict[str, Any]:
        row = {
            "import_issue_uuid": new_uuid(),
            "import_run_uuid": import_run_uuid,
            "severity": issue.severity,
            "issue_code": issue.issue_code,
            "artifact_path": issue.artifact_path,
            "source_record_id": issue.source_record_id,
            "message": issue.message,
            "details_ijson": dump_ijson(issue.details),
            "created_at": utc_now(),
            "schema_version": 1,
        }
        with self.connection:
            self.sqlite.insert("import_issues", row)
        return row

    def list_issues(self, import_run_uuid: str) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in self.connection.execute(
                "SELECT * FROM import_issues WHERE import_run_uuid = ? ORDER BY created_at",
                (import_run_uuid,),
            )
        ]

    def add_mapping(
        self,
        import_run_uuid: str,
        source_platform: str,
        source_object_type: str,
        source_fingerprint: str,
        target_object_type: str,
        target_uuid: str,
        source_object_id: str | None = None,
    ) -> dict[str, Any]:
        existing = self.connection.execute(
            """
            SELECT *
            FROM import_mappings
            WHERE source_platform = ?
              AND source_object_type = ?
              AND COALESCE(source_object_id, source_fingerprint) = COALESCE(?, ?)
            """,
            (source_platform, source_object_type, source_object_id, source_fingerprint),
        ).fetchone()
        if existing is not None:
            return dict(existing)
        mapping_uuid = new_uuid()
        row = {
            "import_mapping_uuid": mapping_uuid,
            "mapping_uuid": mapping_uuid,
            "source_platform": source_platform,
            "source_object_type": source_object_type,
            "source_object_id": source_object_id,
            "source_fingerprint": source_fingerprint,
            "target_object_type": target_object_type,
            "source_type": source_object_type,
            "source_uuid": source_object_id or source_fingerprint,
            "target_type": target_object_type,
            "target_uuid": target_uuid,
            "import_run_uuid": import_run_uuid,
            "created_at": utc_now(),
            "schema_version": 1,
        }
        with self.connection:
            self.sqlite.insert("import_mappings", row)
        return row


def strip_none(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): strip_none(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return [strip_none(item) for item in value]
    return value
