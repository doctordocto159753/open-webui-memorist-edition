import shutil
import sqlite3
from pathlib import Path
from typing import Any

from memcore.config import get_settings
from memcore.imports.adapters.registry import adapter_by_id, probe_all
from memcore.imports.commit_batches import ImportCommitBatchRepository
from memcore.imports.models import ImportIssue, StagedArtifact
from memcore.imports.progress import ensure_progress, get_progress, update_progress
from memcore.imports.reconstruction.content_parts import visible_text
from memcore.imports.reconstruction.graph import branch_count
from memcore.imports.reconstruction.normalizer import normalize_conversation
from memcore.imports.repositories import ImportRepository, strip_none
from memcore.imports.staging import sha256_file, stage_archive
from memcore.models import CreatorType, MessageRole, new_uuid, utc_now
from memcore.repositories import (
    MessageRepository,
    SessionRepository,
    WorkspaceRepository,
)
from memcore.repositories.domain import JobRepository, RepositoryError
from memcore.storage.write_actor import get_write_actor
from memcore.validators.ijson import canonical_hash_ijson, dump_ijson, load_ijson


class ImportService:
    def __init__(self, connection: sqlite3.Connection, object_store_path: str) -> None:
        self.connection = connection
        self.object_store_path = object_store_path
        self.repository = ImportRepository(connection)

    def upload(
        self,
        archive_path: str,
        mode: str = "inspect",
        options: dict[str, Any] | None = None,
        target_workspace_uuid: str | None = None,
        target_project_uuid: str | None = None,
    ) -> dict[str, Any]:
        archive_hash = sha256_file(archive_path)
        run = self.repository.create_run(
            archive_hash,
            mode,
            options or {},
            target_workspace_uuid=target_workspace_uuid,
            target_project_uuid=target_project_uuid,
        )
        artifacts = stage_archive(archive_path, self.object_store_path, run["import_run_uuid"])
        for artifact in artifacts:
            self.repository.add_artifact(run["import_run_uuid"], artifact)
        return self.repository.update_run(
            run["import_run_uuid"],
            {"total_files": len(artifacts), "status": "staged"},
        )

    def inspect(self, import_run_uuid: str) -> dict[str, Any]:
        artifacts = self._staged_artifacts(import_run_uuid)
        probes = probe_all(artifacts)
        candidates = [probe.model_dump(mode="json") for probe in probes if probe.confidence > 0]
        best = probes[0] if probes else None
        values: dict[str, Any] = {
            "status": "inspected",
            "report_ijson": dump_ijson({"adapter_candidates": candidates}),
            "warning_count": sum(len(probe.warnings) for probe in probes),
        }
        if best and best.confidence > 0:
            values.update(
                {
                    "source_platform": best.adapter_id,
                    "detected_format": best.detected_format,
                    "detected_format_version": best.detected_format_version,
                    "schema_fingerprint": canonical_hash_ijson(best.model_dump(mode="json")),
                }
            )
        if len(candidates) > 1 and candidates[0]["confidence"] - candidates[1]["confidence"] < 0.2:
            self.repository.add_issue(
                import_run_uuid,
                ImportIssue(
                    severity="warning",
                    issue_code="ambiguous_adapter_detection",
                    message="Multiple adapters matched with similar confidence",
                    details={"candidates": candidates[:3]},
                ),
            )
        return self.repository.update_run(import_run_uuid, values)

    def reconstruct(self, import_run_uuid: str, adapter_id: str | None = None) -> dict[str, Any]:
        run = self.repository.get_run(import_run_uuid)
        selected_adapter_id = adapter_id or run["source_platform"]
        if not selected_adapter_id:
            raise RepositoryError("No import adapter selected")
        adapter = adapter_by_id(selected_adapter_id)
        artifacts = self._staged_artifacts(import_run_uuid)
        records = list(adapter.parse(artifacts))
        validation = adapter.validate(records)
        for issue in validation.issues:
            self.repository.add_issue(import_run_uuid, issue)
        for index, record in enumerate(records):
            self.repository.add_record(import_run_uuid, record, index)
            conversation = normalize_conversation(record.normalized_payload)
            for issue in conversation.reconstruction_issues:
                self.repository.add_issue(import_run_uuid, issue)
            self._upsert_imported_conversation(
                import_run_uuid, strip_none(conversation.model_dump(mode="json"))
            )
        return self.repository.update_run(
            import_run_uuid,
            {
                "status": "reconstructed",
                "total_conversations": len(records),
                "total_messages": sum(
                    len(normalize_conversation(record.normalized_payload).messages)
                    for record in records
                ),
                "warning_count": validation.warning_count,
                "error_count": validation.error_count,
            },
        )

    def conversations(self, import_run_uuid: str) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in self.connection.execute(
                """
                SELECT *
                FROM imported_conversations
                WHERE import_run_uuid = ?
                ORDER BY created_at, imported_conversation_uuid
                """,
                (import_run_uuid,),
            )
        ]

    def preview_conversation(self, import_run_uuid: str, source_id: str) -> dict[str, Any]:
        row = self.connection.execute(
            """
            SELECT *
            FROM imported_conversations
            WHERE import_run_uuid = ?
              AND COALESCE(source_conversation_id, conversation_fingerprint) = ?
            """,
            (import_run_uuid, source_id),
        ).fetchone()
        if row is None:
            raise RepositoryError("Imported conversation not found")
        return dict(row)

    def dry_run(self, import_run_uuid: str) -> dict[str, Any]:
        conversations = self.conversations(import_run_uuid)
        decisions = []
        expected_messages = 0
        for row in conversations:
            payload = load_ijson(row["normalized_conversation_ijson"])
            conversation = normalize_conversation(payload)
            expected_messages += len(conversation.messages)
            decision = self._duplicate_decision(
                str(payload["source_platform"]),
                row["source_conversation_id"],
                row["conversation_fingerprint"],
            )
            decisions.append(
                {
                    "source_conversation_id": row["source_conversation_id"],
                    "conversation_fingerprint": row["conversation_fingerprint"],
                    "decision": decision,
                    "message_count": len(conversation.messages),
                    "branch_count": branch_count(conversation),
                }
            )
        report = {
            "conversation_count": len(conversations),
            "message_count": expected_messages,
            "decisions": decisions,
            "expected_new_database_rows": {
                "sessions": sum(1 for item in decisions if item["decision"] == "new"),
                "messages": sum(
                    item["message_count"] for item in decisions if item["decision"] == "new"
                ),
            },
            "privacy_sensitivity_warnings": [],
        }
        plan_fingerprint = canonical_hash_ijson(report)
        with self.connection:
            self.connection.execute(
                """
                INSERT OR REPLACE INTO import_dry_run_reports (
                    import_run_uuid, report_ijson, plan_fingerprint, created_at, schema_version
                )
                VALUES (?, ?, ?, ?, 1)
                """,
                (import_run_uuid, dump_ijson(report), plan_fingerprint, utc_now()),
            )
        return self.repository.update_run(
            import_run_uuid,
            {
                "status": "dry_run",
                "report_ijson": dump_ijson(report),
                "skipped_records": sum(1 for item in decisions if item["decision"] != "new"),
            },
        )

    def dry_run_report(self, import_run_uuid: str) -> dict[str, Any]:
        row = self.connection.execute(
            "SELECT * FROM import_dry_run_reports WHERE import_run_uuid = ?",
            (import_run_uuid,),
        ).fetchone()
        if row is None:
            raise RepositoryError("Dry-run report not found")
        return dict(row)

    def progress(self, import_run_uuid: str) -> dict[str, Any]:
        run = self.repository.get_run(import_run_uuid)
        progress = ensure_progress(
            self.connection,
            import_run_uuid,
            phase=str(run["status"]),
            records_total=int(run["total_conversations"] or 0),
        )
        return {"status": run["status"], **progress}

    def pause(self, import_run_uuid: str) -> dict[str, Any]:
        self.repository.update_run(import_run_uuid, {"status": "paused"})
        return update_progress(self.connection, import_run_uuid, paused=1, phase="paused")

    def resume(self, import_run_uuid: str) -> dict[str, Any]:
        self.repository.update_run(import_run_uuid, {"status": "dry_run"})
        return update_progress(
            self.connection,
            import_run_uuid,
            paused=0,
            cancelled=0,
            throttled=0,
            throttle_reason=None,
            phase="resumed",
        )

    def commit(
        self,
        import_run_uuid: str,
        processing_mode: str = "none",
        batch_size: int = 100,
        max_write_queue_depth: int = 500,
    ) -> dict[str, Any]:
        result = self.commit_next_batch(
            import_run_uuid,
            processing_mode=processing_mode,
            batch_size=batch_size,
            max_write_queue_depth=max_write_queue_depth,
        )
        while result["status"] == "committing":
            result = self.commit_next_batch(
                import_run_uuid,
                processing_mode=processing_mode,
                batch_size=batch_size,
                max_write_queue_depth=max_write_queue_depth,
            )
        return result

    def commit_next_batch(
        self,
        import_run_uuid: str,
        processing_mode: str = "none",
        batch_size: int = 100,
        max_write_queue_depth: int = 500,
    ) -> dict[str, Any]:
        dry_run = self.dry_run_report(import_run_uuid)
        if dry_run["plan_fingerprint"] != canonical_hash_ijson(load_ijson(dry_run["report_ijson"])):
            raise RepositoryError("dry-run plan fingerprint mismatch")
        run = self.repository.get_run(import_run_uuid)
        if str(run["status"]) == "cancelled":
            return self.repository.get_run(import_run_uuid)
        if str(run["status"]) == "paused":
            return self.repository.get_run(import_run_uuid)
        run = self.repository.update_run(import_run_uuid, {"status": "committing"})
        total_conversations = self._conversation_count(import_run_uuid)
        ensure_progress(
            self.connection,
            import_run_uuid,
            phase="commit",
            records_total=total_conversations,
        )
        workspace_uuid = run["target_workspace_uuid"]
        if not workspace_uuid:
            workspace_uuid = (
                WorkspaceRepository(self.connection)
                .create_workspace("Imported conversations")
                .workspace_uuid
            )
            run = self.repository.update_run(
                import_run_uuid,
                {"target_workspace_uuid": workspace_uuid},
            )

        pending_conversations = self._pending_commit_conversations(import_run_uuid, batch_size)
        if not pending_conversations:
            return self._finalize_commit_if_complete(import_run_uuid, run)

        batch_repository = ImportCommitBatchRepository(self.connection)
        batch = batch_repository.start_batch(import_run_uuid, pending_conversations)
        for row in pending_conversations:
            progress = get_progress(self.connection, import_run_uuid)
            if progress["paused"] or progress["cancelled"]:
                return self.repository.update_run(import_run_uuid, {"status": "paused"})
            if _write_queue_depth() >= max_write_queue_depth:
                committed_count, failed_count = self._terminal_counts(import_run_uuid)
                update_progress(
                    self.connection,
                    import_run_uuid,
                    throttled=1,
                    throttle_reason="write_queue_depth",
                    paused=1,
                    phase="paused",
                    records_done=committed_count,
                    records_failed=failed_count,
                )
                return self.repository.update_run(import_run_uuid, {"status": "paused"})
            payload = load_ijson(row["normalized_conversation_ijson"])
            if (
                self._duplicate_decision(
                    str(payload["source_platform"]),
                    row["source_conversation_id"],
                    row["conversation_fingerprint"],
                )
                != "new"
            ):
                self._mark_imported_conversation(
                    row["imported_conversation_uuid"],
                    commit_status="skipped",
                )
                continue
            try:
                session_uuid, _message_count = self._commit_conversation(
                    import_run_uuid,
                    workspace_uuid,
                    run["target_project_uuid"],
                    row,
                    payload,
                    processing_mode,
                )
                self._mark_imported_conversation(
                    row["imported_conversation_uuid"],
                    commit_status="committed",
                    target_session_uuid=session_uuid,
                )
            except Exception as error:
                self._mark_imported_conversation(
                    row["imported_conversation_uuid"],
                    commit_status="failed",
                )
                self.repository.add_issue(
                    import_run_uuid,
                    ImportIssue(
                        severity="warning",
                        issue_code="conversation_commit_failed",
                        message=(
                            "Conversation failed during bounded commit: "
                            f"{type(error).__name__}"
                        ),
                        details={"source_conversation_id": row["source_conversation_id"]},
                    ),
                )
            committed_count, failed_count = self._terminal_counts(import_run_uuid)
            update_progress(
                self.connection,
                import_run_uuid,
                phase="commit",
                records_total=total_conversations,
                records_done=committed_count,
                records_failed=failed_count,
                current_batch=committed_count // max(1, batch_size),
            )
        batch_repository.finish_batch(batch["batch_uuid"], "committed")
        counters = self._commit_counters(import_run_uuid)
        run = self.repository.update_run(
            import_run_uuid,
            {
                "status": "committing",
                "imported_conversations": counters["imported_conversations"],
                "imported_messages": counters["imported_messages"],
                "skipped_records": counters["skipped_records"],
                "error_count": max(int(run["error_count"] or 0), counters["failed_records"]),
            },
        )
        return self._finalize_commit_if_complete(import_run_uuid, run)

    def cancel(self, import_run_uuid: str) -> dict[str, Any]:
        self.repository.update_run(import_run_uuid, {"status": "cancelled"})
        return update_progress(self.connection, import_run_uuid, cancelled=1, phase="cancelled")

    def delete_staging(self, import_run_uuid: str) -> dict[str, str]:
        run_dir = Path(self.object_store_path) / "imports" / import_run_uuid
        if run_dir.exists():
            shutil.rmtree(run_dir)
        return {"status": "deleted"}

    def _commit_conversation(
        self,
        import_run_uuid: str,
        workspace_uuid: str,
        project_uuid: str | None,
        row: dict[str, Any],
        payload: dict[str, Any],
        processing_mode: str,
    ) -> tuple[str, int]:
        conversation = normalize_conversation(payload)
        session = SessionRepository(self.connection).create_session(
            workspace_uuid=workspace_uuid,
            project_uuid=project_uuid,
            title=conversation.title,
        )
        self.repository.add_mapping(
            import_run_uuid,
            conversation.source_platform,
            "conversation",
            row["conversation_fingerprint"],
            "session",
            session.session_uuid,
            source_object_id=conversation.source_conversation_id,
        )
        message_uuid_by_source: dict[str, str] = {}
        for source_message_id, message in conversation.messages.items():
            role = _message_role(message.role.value)
            creator = _creator_type(message.role.value)
            raw_text = visible_text(message.content_parts)
            created = MessageRepository(self.connection).create_message(
                session.session_uuid,
                role=role,
                creator_type=creator,
                raw_text=raw_text,
                snapshot={
                    "provider_timestamp": message.created_at,
                    "branch_status": message.branch_status,
                    "source_message_id": source_message_id,
                    "metadata": message.metadata,
                },
                job_priority=30,
            )
            message_uuid_by_source[source_message_id] = created.message_uuid
            self.repository.add_mapping(
                import_run_uuid,
                conversation.source_platform,
                "message",
                canonical_hash_ijson(strip_none(message.model_dump(mode="json"))),
                "message",
                created.message_uuid,
                source_object_id=message.source_message_id,
            )
            if processing_mode in {"extract_candidates", "full_memory_reconstruction"}:
                JobRepository(self.connection).enqueue_job_once(
                    "text_unitization",
                    {"message_uuid": created.message_uuid, "session_uuid": session.session_uuid},
                    priority=30,
                )
        for source_message_id, message in conversation.messages.items():
            if message.parent_ids:
                parent_uuid = message_uuid_by_source.get(message.parent_ids[0])
                child_uuid = message_uuid_by_source.get(source_message_id)
                if parent_uuid and child_uuid:
                    self.connection.execute(
                        "UPDATE messages SET parent_message_uuid = ? WHERE message_uuid = ?",
                        (parent_uuid, child_uuid),
                    )
        return session.session_uuid, len(conversation.messages)

    def _duplicate_decision(
        self,
        source_platform: str,
        source_conversation_id: str | None,
        fingerprint: str,
    ) -> str:
        row = self.connection.execute(
            """
            SELECT *
            FROM import_mappings
            WHERE source_platform = ?
              AND source_object_type = 'conversation'
              AND COALESCE(source_object_id, source_fingerprint) = COALESCE(?, ?)
            """,
            (source_platform, source_conversation_id, fingerprint),
        ).fetchone()
        return "already_mapped" if row is not None else "new"

    def _conversation_count(self, import_run_uuid: str) -> int:
        return int(
            self.connection.execute(
                """
                SELECT COUNT(*)
                FROM imported_conversations
                WHERE import_run_uuid = ?
                """,
                (import_run_uuid,),
            ).fetchone()[0]
        )

    def _pending_commit_conversations(
        self,
        import_run_uuid: str,
        batch_size: int,
    ) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in self.connection.execute(
                """
                SELECT *
                FROM imported_conversations
                WHERE import_run_uuid = ?
                  AND commit_status = 'pending'
                ORDER BY created_at, imported_conversation_uuid
                LIMIT ?
                """,
                (import_run_uuid, max(1, batch_size)),
            )
        ]

    def _mark_imported_conversation(
        self,
        imported_conversation_uuid: str,
        commit_status: str,
        target_session_uuid: str | None = None,
    ) -> None:
        with self.connection:
            self.connection.execute(
                """
                UPDATE imported_conversations
                SET commit_status = ?, target_session_uuid = COALESCE(?, target_session_uuid)
                WHERE imported_conversation_uuid = ?
                """,
                (commit_status, target_session_uuid, imported_conversation_uuid),
            )

    def _terminal_counts(self, import_run_uuid: str) -> tuple[int, int]:
        row = self.connection.execute(
            """
            SELECT
              SUM(CASE WHEN commit_status IN ('committed', 'skipped', 'failed') THEN 1 ELSE 0 END)
                AS terminal_count,
              SUM(CASE WHEN commit_status = 'failed' THEN 1 ELSE 0 END) AS failed_count
            FROM imported_conversations
            WHERE import_run_uuid = ?
            """,
            (import_run_uuid,),
        ).fetchone()
        return int(row["terminal_count"] or 0), int(row["failed_count"] or 0)

    def _commit_counters(self, import_run_uuid: str) -> dict[str, int]:
        conversations = self.connection.execute(
            """
            SELECT
              SUM(CASE WHEN commit_status = 'committed' THEN 1 ELSE 0 END)
                AS imported_conversations,
              SUM(CASE WHEN commit_status = 'skipped' THEN 1 ELSE 0 END) AS skipped_records,
              SUM(CASE WHEN commit_status = 'failed' THEN 1 ELSE 0 END) AS failed_records
            FROM imported_conversations
            WHERE import_run_uuid = ?
            """,
            (import_run_uuid,),
        ).fetchone()
        messages = self.connection.execute(
            """
            SELECT COUNT(*)
            FROM import_mappings
            WHERE import_run_uuid = ?
              AND source_object_type = 'message'
              AND target_object_type = 'message'
            """,
            (import_run_uuid,),
        ).fetchone()[0]
        return {
            "imported_conversations": int(conversations["imported_conversations"] or 0),
            "imported_messages": int(messages or 0),
            "skipped_records": int(conversations["skipped_records"] or 0),
            "failed_records": int(conversations["failed_records"] or 0),
        }

    def _finalize_commit_if_complete(
        self,
        import_run_uuid: str,
        run: dict[str, Any],
    ) -> dict[str, Any]:
        total = self._conversation_count(import_run_uuid)
        terminal_count, failed_count = self._terminal_counts(import_run_uuid)
        counters = self._commit_counters(import_run_uuid)
        if terminal_count < total:
            return self.repository.update_run(
                import_run_uuid,
                {
                    "status": "committing",
                    "imported_conversations": counters["imported_conversations"],
                    "imported_messages": counters["imported_messages"],
                    "skipped_records": counters["skipped_records"],
                },
            )
        update_progress(
            self.connection,
            import_run_uuid,
            phase="committed",
            records_total=total,
            records_done=terminal_count,
            records_failed=failed_count,
        )
        return self.repository.update_run(
            import_run_uuid,
            {
                "status": "committed",
                "imported_conversations": counters["imported_conversations"],
                "imported_messages": counters["imported_messages"],
                "skipped_records": counters["skipped_records"],
                "error_count": max(int(run["error_count"] or 0), failed_count),
                "completed_at": utc_now(),
            },
        )

    def _staged_artifacts(self, import_run_uuid: str) -> list[StagedArtifact]:
        return [
            StagedArtifact(
                relative_path=row["relative_path"],
                artifact_role=row["artifact_role"],
                detected_media_type=row["detected_media_type"],
                size_bytes=row["size_bytes"],
                sha256=row["sha256"],
                object_store_path=row["object_store_path"],
            )
            for row in self.repository.list_artifacts(import_run_uuid)
        ]

    def _upsert_imported_conversation(
        self,
        import_run_uuid: str,
        conversation_payload: dict[str, Any],
    ) -> None:
        clean_payload = strip_none(conversation_payload)
        fingerprint = canonical_hash_ijson(
            {
                "source_platform": clean_payload["source_platform"],
                "source_conversation_id": clean_payload.get("source_conversation_id"),
                "roots": clean_payload.get("roots", []),
                "messages": clean_payload.get("messages", {}),
            }
        )
        with self.connection:
            self.connection.execute(
                """
                INSERT OR IGNORE INTO imported_conversations (
                    imported_conversation_uuid, import_run_uuid, source_platform,
                    source_conversation_id, conversation_fingerprint,
                    normalized_conversation_ijson, dry_run_decision, commit_status,
                    target_session_uuid, created_at, schema_version
                )
                VALUES (?, ?, ?, ?, ?, ?, 'new', 'pending', NULL, ?, 1)
                """,
                (
                    new_uuid(),
                    import_run_uuid,
                    clean_payload["source_platform"],
                    clean_payload.get("source_conversation_id"),
                    fingerprint,
                    dump_ijson(clean_payload),
                    utc_now(),
                ),
            )


def _message_role(role: str) -> MessageRole:
    if role == "assistant":
        return MessageRole.ASSISTANT
    if role == "system":
        return MessageRole.SYSTEM
    if role == "tool":
        return MessageRole.TOOL
    return MessageRole.USER if role == "user" else MessageRole.SYSTEM


def _creator_type(role: str) -> CreatorType:
    if role == "assistant":
        return CreatorType.MODEL
    if role == "tool":
        return CreatorType.TOOL
    if role == "system":
        return CreatorType.SYSTEM
    return CreatorType.USER


def _write_queue_depth() -> int:
    try:
        settings = get_settings()
        return int(get_write_actor(settings.db_path).diagnostics()["queue_depth"])
    except Exception:
        return 0
