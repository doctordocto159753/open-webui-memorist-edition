import shutil
import sqlite3
from pathlib import Path
from typing import Any

from memcore.config import get_settings
from memcore.imports.adapters.registry import adapter_by_id, probe_all
from memcore.imports.commit_batches import ImportCommitBatchRepository
from memcore.imports.models import ImportIssue, StagedArtifact
from memcore.imports.processing import ImportMessageProcessor
from memcore.imports.progress import ensure_progress, get_progress, update_progress
from memcore.imports.reconstruction.content_parts import visible_text
from memcore.imports.reconstruction.graph import branch_count
from memcore.imports.reconstruction.normalizer import normalize_conversation
from memcore.imports.repositories import ImportRepository, strip_none
from memcore.imports.staging import sha256_file, stage_import_source, stage_trusted_upload_file
from memcore.models import CreatorType, MessageRole, new_uuid, utc_now
from memcore.repositories import (
    MessageRepository,
    SessionRepository,
    WorkspaceRepository,
)
from memcore.repositories.domain import RepositoryError
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
        artifacts = stage_import_source(
            archive_path, self.object_store_path, run["import_run_uuid"]
        )
        for artifact in artifacts:
            self.repository.add_artifact(run["import_run_uuid"], artifact)
        return self.repository.update_run(
            run["import_run_uuid"],
            {"total_files": len(artifacts), "status": "staged"},
        )

    def upload_staged_file(
        self,
        staged_path: str,
        archive_sha256: str,
        original_filename: str,
        mode: str = "inspect",
        options: dict[str, Any] | None = None,
        target_workspace_uuid: str | None = None,
        target_project_uuid: str | None = None,
    ) -> dict[str, Any]:
        run = self.repository.create_run(
            archive_sha256,
            mode,
            {
                **(options or {}),
                "original_filename": original_filename,
                "upload_contract": "multipart",
            },
            target_workspace_uuid=target_workspace_uuid,
            target_project_uuid=target_project_uuid,
        )
        try:
            artifacts = stage_trusted_upload_file(
                staged_path,
                self.object_store_path,
                run["import_run_uuid"],
                original_filename,
                archive_sha256,
            )
            for artifact in artifacts:
                self.repository.add_artifact(run["import_run_uuid"], artifact)
            return self.repository.update_run(
                run["import_run_uuid"],
                {"total_files": len(artifacts), "status": "staged"},
            )
        except Exception:
            shutil.rmtree(
                Path(self.object_store_path) / "imports" / run["import_run_uuid"],
                ignore_errors=True,
            )
            raise

    def inspect(self, import_run_uuid: str) -> dict[str, Any]:
        artifacts = self._staged_artifacts(import_run_uuid)
        probes = probe_all(artifacts)
        candidates = [probe.model_dump(mode="json") for probe in probes if probe.confidence > 0]
        best = probes[0] if probes else None
        values: dict[str, Any] = {
            "status": "inspected",
            "report_ijson": dump_ijson(
                {
                    "adapter_candidates": candidates,
                    "source_platform_display": (
                        "ChatGPT/OpenAI"
                        if best and best.confidence > 0 and best.adapter_id == "chatgpt"
                        else None
                    ),
                    "file_count": len(artifacts),
                }
            ),
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
        if not candidates:
            self.repository.add_issue(
                import_run_uuid,
                ImportIssue(
                    severity="error",
                    issue_code="unrecognized_or_malformed_import_source",
                    message=(
                        "No supported conversation export was detected; verify that JSON is "
                        "well-formed and uses a supported provider format"
                    ),
                ),
            )
            values["error_count"] = int(values.get("error_count", 0)) + 1
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

    def dry_run(
        self, import_run_uuid: str, processing_mode: str | None = None
    ) -> dict[str, Any]:
        run = self.repository.get_run(import_run_uuid)
        run_options = load_ijson(run["options_ijson"])
        selected_mode = processing_mode or str(run_options.get("processing_mode") or "none")
        processor = ImportMessageProcessor(self.connection, get_settings())
        processor.validate_mode(selected_mode)
        model_target = processor.model_target(
            run.get("target_workspace_uuid"), run.get("target_project_uuid")
        )
        conversations = self.conversations(import_run_uuid)
        decisions = []
        expected_messages = 0
        eligible_messages = 0
        eligible_new_messages = 0
        skip_reasons: dict[str, int] = {}
        duplicate_messages = 0
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
            for message in conversation.messages.values():
                eligible, reason, _text = processor.eligibility(message)
                if decision != "new":
                    duplicate_messages += 1
                if eligible:
                    eligible_messages += 1
                    if decision == "new":
                        eligible_new_messages += 1
                else:
                    reason_key = reason or "unknown_ineligible_reason"
                    skip_reasons[reason_key] = skip_reasons.get(reason_key, 0) + 1
        expected_new_sessions = sum(1 for item in decisions if item["decision"] == "new")
        expected_new_messages = sum(
            item["message_count"] for item in decisions if item["decision"] == "new"
        )
        report = {
            "source_platform": run.get("source_platform"),
            "source_platform_display": (
                "ChatGPT/OpenAI"
                if run.get("source_platform") == "chatgpt"
                else run.get("source_platform")
            ),
            "detected_format": run.get("detected_format"),
            "conversation_count": len(conversations),
            "message_count": expected_messages,
            "eligible_processing_message_count": eligible_messages,
            "ineligible_processing_message_count": sum(skip_reasons.values()),
            "skip_reasons": skip_reasons,
            "duplicate_conversation_count": sum(
                1 for item in decisions if item["decision"] != "new"
            ),
            "duplicate_message_count": duplicate_messages,
            "decisions": decisions,
            "expected_new_database_rows": {
                "sessions": expected_new_sessions,
                "messages": expected_new_messages,
                "import_mappings": expected_new_sessions + expected_new_messages,
            },
            "expected_text_unitization_jobs": (
                eligible_new_messages if selected_mode == "extract_candidates" else 0
            ),
            "expected_memory_processing_jobs": (
                eligible_new_messages
                if selected_mode == "full_memory_reconstruction"
                else 0
            ),
            "processing_mode": selected_mode,
            "processing_priority": "low",
            "large_reconstruction_warning": (
                "Full reconstruction processes every eligible message and may consume "
                "substantial time and tokens. No cost-based sampling is performed."
                if selected_mode == "full_memory_reconstruction"
                else None
            ),
            "processing_model": model_target,
            "memory_extraction_default_configured": bool(
                model_target.get("model_profile_uuid")
            ),
            "import_reconstruction_default_required": False,
            "deterministic_fallback": bool(model_target.get("deterministic_fallback")),
            "graph_projection_enabled": bool(get_settings().enable_graph_projection),
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
        stored_progress = ensure_progress(
            self.connection,
            import_run_uuid,
            phase=str(run["status"]),
            records_total=int(run["total_conversations"] or 0),
        )
        processing = ImportMessageProcessor(
            self.connection, get_settings()
        ).processing_report(import_run_uuid)
        return {
            "status": run["status"],
            **stored_progress,
            "import_run_uuid": import_run_uuid,
            "phase": run["status"],
            "conversations_total": int(run["total_conversations"] or 0),
            "conversations_committed": int(run["imported_conversations"] or 0),
            "messages_total": int(run["total_messages"] or 0),
            "messages_committed": int(run["imported_messages"] or 0),
            "messages_eligible_for_processing": (
                processing["processing_jobs_total"] - processing["processing_jobs_skipped"]
            ),
            "started_at": run["created_at"],
            "finished_at": run["completed_at"],
            "last_error_sanitized": self._last_processing_error(import_run_uuid),
            **processing,
        }

    def pause(self, import_run_uuid: str) -> dict[str, Any]:
        self.repository.update_run(import_run_uuid, {"status": "paused"})
        return update_progress(self.connection, import_run_uuid, paused=1, phase="paused")

    def resume(self, import_run_uuid: str) -> dict[str, Any]:
        processing_count = self.connection.execute(
            "SELECT COUNT(*) FROM import_message_processing_status WHERE import_run_uuid = ?",
            (import_run_uuid,),
        ).fetchone()[0]
        self.repository.update_run(
            import_run_uuid, {"status": "processing" if processing_count else "dry_run"}
        )
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
        processor = ImportMessageProcessor(self.connection, get_settings())
        try:
            processor.validate_mode(processing_mode)
        except ValueError as error:
            raise RepositoryError(str(error)) from error
        dry_run = self.dry_run_report(import_run_uuid)
        dry_run_report = load_ijson(dry_run["report_ijson"])
        if dry_run["plan_fingerprint"] != canonical_hash_ijson(dry_run_report):
            raise RepositoryError("dry-run plan fingerprint mismatch")
        if str(dry_run_report.get("processing_mode") or "none") != processing_mode:
            raise RepositoryError(
                "commit processing_mode differs from the approved dry-run; rerun dry-run"
            )
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
        model_target = processor.model_target(workspace_uuid, run["target_project_uuid"])

        pending_conversations = self._pending_commit_conversations(import_run_uuid, batch_size)
        if not pending_conversations:
            return self._finalize_commit_if_complete(import_run_uuid, run, processing_mode)

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
                self._record_duplicate_processing(
                    import_run_uuid,
                    payload,
                    processing_mode,
                    model_target,
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
                    model_target,
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
                            f"{type(error).__name__}: {error}"
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
        return self._finalize_commit_if_complete(import_run_uuid, run, processing_mode)

    def process_next_batch(
        self, import_run_uuid: str, batch_size: int = 25
    ) -> dict[str, Any]:
        return ImportMessageProcessor(
            self.connection, get_settings()
        ).process_next_batch(import_run_uuid, batch_size)

    def retry_failed_processing(self, import_run_uuid: str) -> dict[str, Any]:
        return ImportMessageProcessor(
            self.connection, get_settings()
        ).retry_failed(import_run_uuid)

    def processing_report(self, import_run_uuid: str) -> dict[str, Any]:
        return ImportMessageProcessor(
            self.connection, get_settings()
        ).processing_report(import_run_uuid)

    def message_processing_statuses(self, import_run_uuid: str) -> list[dict[str, Any]]:
        return ImportMessageProcessor(
            self.connection, get_settings()
        ).statuses(import_run_uuid)

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
        model_target: dict[str, Any],
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
        ordered_messages = sorted(
            conversation.messages.items(),
            key=lambda item: (
                item[1].created_at is None,
                str(item[1].created_at or ""),
                item[0],
            ),
        )
        for source_message_id, message in ordered_messages:
            role = _message_role(message.role.value)
            creator = _creator_type(message.role.value)
            raw_text = visible_text(message.content_parts)
            created = MessageRepository(self.connection).create_message(
                session.session_uuid,
                role=role,
                creator_type=creator,
                raw_text=raw_text,
                snapshot={
                    "import_run_uuid": import_run_uuid,
                    "source_platform": conversation.source_platform,
                    "source_conversation_id": conversation.source_conversation_id,
                    "provider_timestamp": message.created_at,
                    "branch_status": message.branch_status,
                    "source_message_id": source_message_id,
                    "metadata": message.metadata,
                    "imported_content_is_untrusted": True,
                },
                job_priority=30,
                enqueue_text_unitization=False,
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
            ImportMessageProcessor(self.connection, get_settings()).schedule(
                import_run_uuid=import_run_uuid,
                source_platform=conversation.source_platform,
                source_conversation_id=conversation.source_conversation_id,
                source_message_id=source_message_id,
                target_session_uuid=session.session_uuid,
                target_message_uuid=created.message_uuid,
                message=message,
                processing_mode=processing_mode,
                model_target=model_target,
            )
        for source_message_id, message in ordered_messages:
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

    def _record_duplicate_processing(
        self,
        import_run_uuid: str,
        payload: dict[str, Any],
        processing_mode: str,
        model_target: dict[str, Any],
    ) -> None:
        conversation = normalize_conversation(payload)
        processor = ImportMessageProcessor(self.connection, get_settings())
        for source_message_id in conversation.messages:
            mapping = self.connection.execute(
                """
                SELECT im.target_uuid, m.session_uuid
                FROM import_mappings im
                LEFT JOIN messages m ON m.message_uuid = im.target_uuid
                WHERE source_platform = ? AND source_object_type = 'message'
                  AND source_object_id = ?
                LIMIT 1
                """,
                (conversation.source_platform, source_message_id),
            ).fetchone()
            processor.record_duplicate(
                import_run_uuid=import_run_uuid,
                source_platform=conversation.source_platform,
                source_conversation_id=conversation.source_conversation_id,
                source_message_id=source_message_id,
                target_session_uuid=(str(mapping["session_uuid"]) if mapping else None),
                target_message_uuid=str(mapping["target_uuid"]) if mapping else None,
                processing_mode=processing_mode,
                model_target=model_target,
            )

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
        processing_mode: str,
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
        final_phase = (
            "processing" if processing_mode == "full_memory_reconstruction" else "committed"
        )
        update_progress(
            self.connection,
            import_run_uuid,
            phase=final_phase,
            records_total=total,
            records_done=terminal_count,
            records_failed=failed_count,
        )
        committed = self.repository.update_run(
            import_run_uuid,
            {
                "status": final_phase,
                "imported_conversations": counters["imported_conversations"],
                "imported_messages": counters["imported_messages"],
                "skipped_records": counters["skipped_records"],
                "error_count": max(int(run["error_count"] or 0), failed_count),
                "completed_at": (
                    None if processing_mode == "full_memory_reconstruction" else utc_now()
                ),
            },
        )
        if processing_mode == "full_memory_reconstruction":
            ImportMessageProcessor(self.connection, get_settings()).finalize_if_terminal(
                import_run_uuid
            )
            return self.repository.get_run(import_run_uuid)
        return committed

    def _last_processing_error(self, import_run_uuid: str) -> str | None:
        row = self.connection.execute(
            """
            SELECT error_sanitized FROM import_message_processing_status
            WHERE import_run_uuid = ? AND error_sanitized IS NOT NULL
            ORDER BY updated_at DESC LIMIT 1
            """,
            (import_run_uuid,),
        ).fetchone()
        return str(row["error_sanitized"]) if row else None

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
