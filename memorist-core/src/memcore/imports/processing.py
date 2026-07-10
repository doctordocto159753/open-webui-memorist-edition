from __future__ import annotations

import hashlib
import os
import sqlite3
from collections import Counter
from typing import Any

from memcore.config import Settings
from memcore.imports.reconstruction.content_parts import visible_text
from memcore.imports.reconstruction.models import ImportedMessageNode
from memcore.memory_worker.pipeline import MemoryWorkerPipeline
from memcore.model_control.repository import ModelControlRepository
from memcore.model_control.schemas import UsageEventCreate
from memcore.models import ModelRole, new_uuid, utc_now
from memcore.repositories import JobRepository

PROCESSING_MODES = {"none", "extract_candidates", "full_memory_reconstruction"}
TERMINAL_STATUSES = {"succeeded", "skipped", "already_processed", "failed"}


class ImportMessageProcessor:
    def __init__(self, connection: sqlite3.Connection, settings: Settings) -> None:
        self.connection = connection
        self.settings = settings
        self.model_control = ModelControlRepository(connection)

    def validate_mode(self, processing_mode: str) -> str:
        if processing_mode not in PROCESSING_MODES:
            allowed = ", ".join(sorted(PROCESSING_MODES))
            raise ValueError(f"unsupported processing_mode; expected one of: {allowed}")
        return processing_mode

    def model_target(
        self, workspace_uuid: str | None = None, project_uuid: str | None = None
    ) -> dict[str, Any]:
        profile = self.model_control.resolve_default(
            ModelRole.MEMORY_EXTRACTION,
            workspace_uuid=workspace_uuid,
            project_uuid=project_uuid,
        )
        warnings: list[str] = []
        if profile is None:
            return {
                "role": ModelRole.MEMORY_EXTRACTION.value,
                "model_profile_uuid": None,
                "provider_type": "deterministic",
                "model_name": "deterministic_extraction",
                "deterministic_fallback": True,
                "profile_enabled": True,
                "privacy_acknowledged": True,
                "secret_configured": True,
                "warnings": warnings,
            }

        enabled = bool(profile.get("is_enabled", True))
        requires_ack = bool(profile.get("requires_privacy_acknowledgement", False))
        privacy_acknowledged = bool(profile.get("privacy_acknowledged_at")) or not requires_ack
        internal_profile = self.model_control.get_profile(str(profile["model_profile_uuid"]))
        secret_env = internal_profile.secret_env_var_name if internal_profile is not None else None
        secret_configured = not secret_env or bool(os.getenv(str(secret_env)))
        health = self.connection.execute(
            """
            SELECT status, latency_ms, detail_sanitized, created_at
            FROM model_health_events
            WHERE model_profile_uuid = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (profile.get("model_profile_uuid"),),
        ).fetchone()
        if not enabled:
            warnings.append("configured memory_extraction profile is disabled")
        if not privacy_acknowledged:
            warnings.append("configured memory_extraction profile lacks privacy acknowledgement")
        if not secret_configured:
            warnings.append("required provider secret environment variable is missing")
        if health is not None and health["status"] not in {"ok", "healthy"}:
            warnings.append(
                f"latest provider health is {health['status']}; runtime failures remain retryable"
            )
        usable = enabled and privacy_acknowledged and secret_configured
        if not usable:
            warnings.append("deterministic fallback will be used")
            return {
                "role": ModelRole.MEMORY_EXTRACTION.value,
                "model_profile_uuid": None,
                "provider_type": "deterministic",
                "model_name": "deterministic_extraction",
                "deterministic_fallback": True,
                "profile_enabled": enabled,
                "privacy_acknowledged": privacy_acknowledged,
                "secret_configured": secret_configured,
                "warnings": warnings,
            }
        return {
            "role": ModelRole.MEMORY_EXTRACTION.value,
            "model_profile_uuid": profile.get("model_profile_uuid"),
            "provider_type": profile.get("provider_type") or profile.get("provider"),
            "model_name": profile.get("model_name"),
            "deterministic_fallback": False,
            "profile_enabled": enabled,
            "privacy_acknowledged": privacy_acknowledged,
            "secret_configured": secret_configured,
            "latest_health": dict(health) if health is not None else None,
            "warnings": warnings,
        }

    def eligibility(self, message: ImportedMessageNode) -> tuple[bool, str | None, str]:
        text = visible_text(message.content_parts)
        metadata = message.metadata
        if metadata.get("source_node_has_message") is False:
            return False, "null_source_message_node", text
        if not text:
            if any(part.quarantined for part in message.content_parts):
                return False, "provider_internal_reasoning_quarantined", text
            if metadata.get("attachments"):
                return False, "unsupported_binary_only_attachment", text
            return False, "empty_visible_text", text
        if message.role.value not in {"user", "assistant"}:
            return False, "unsupported_role_for_memory_processing", text
        return True, None, text

    def schedule(
        self,
        *,
        import_run_uuid: str,
        source_platform: str,
        source_conversation_id: str | None,
        source_message_id: str,
        target_session_uuid: str,
        target_message_uuid: str,
        message: ImportedMessageNode,
        processing_mode: str,
        model_target: dict[str, Any],
    ) -> dict[str, Any] | None:
        self.validate_mode(processing_mode)
        if processing_mode == "none":
            return None
        eligible, skip_reason, text = self.eligibility(message)
        common = {
            "import_run_uuid": import_run_uuid,
            "source_platform": source_platform,
            "source_conversation_id": source_conversation_id,
            "source_message_id": source_message_id,
            "target_session_uuid": target_session_uuid,
            "target_message_uuid": target_message_uuid,
            "processing_mode": processing_mode,
            "model_profile_uuid": model_target.get("model_profile_uuid"),
            "provider_type": model_target.get("provider_type"),
            "model_name": model_target.get("model_name"),
            "input_content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        }
        if not eligible:
            return self._upsert_status(
                **common,
                processing_stage="eligibility",
                status="skipped",
                skip_reason=skip_reason,
                finished_at=utc_now(),
            )
        already_processed = self.connection.execute(
            """
            SELECT processing_run_uuid
            FROM memory_processing_runs
            WHERE message_uuid = ? AND input_content_hash = ? AND status = 'succeeded'
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (target_message_uuid, common["input_content_hash"]),
        ).fetchone()
        if already_processed is not None:
            return self._upsert_status(
                **common,
                processing_stage="complete",
                status="already_processed",
                memory_processing_run_uuid=already_processed["processing_run_uuid"],
                finished_at=utc_now(),
            )

        job_type = (
            "memory_extraction"
            if processing_mode == "full_memory_reconstruction"
            else "text_unitization"
        )
        payload = {
            "message_uuid": target_message_uuid,
            "session_uuid": target_session_uuid,
            "import_run_uuid": import_run_uuid,
            "source_platform": source_platform,
            "source_conversation_id": source_conversation_id,
            "source_message_id": source_message_id,
            "model_role": ModelRole.MEMORY_EXTRACTION.value,
            "model_profile_uuid": model_target.get("model_profile_uuid"),
        }
        job = JobRepository(self.connection).enqueue_job_once(
            job_type, payload, priority=30, max_attempts=1
        )
        self.model_control.record_usage_event(
            UsageEventCreate(
                role=ModelRole.MEMORY_EXTRACTION,
                stage=f"import_{job_type}_queued",
                model_profile_uuid=model_target.get("model_profile_uuid"),
                session_uuid=target_session_uuid,
                message_uuid=target_message_uuid,
                import_run_uuid=import_run_uuid,
                job_uuid=job.job_uuid,
                status="queued",
            )
        )
        return self._upsert_status(
            **common,
            processing_stage="memory_extraction" if job_type == "memory_extraction" else job_type,
            status="queued",
            job_uuid=job.job_uuid,
        )

    def record_duplicate(
        self,
        *,
        import_run_uuid: str,
        source_platform: str,
        source_conversation_id: str | None,
        source_message_id: str,
        target_session_uuid: str | None,
        target_message_uuid: str | None,
        processing_mode: str,
        model_target: dict[str, Any],
    ) -> dict[str, Any] | None:
        if processing_mode == "none":
            return None
        return self._upsert_status(
            import_run_uuid=import_run_uuid,
            source_platform=source_platform,
            source_conversation_id=source_conversation_id,
            source_message_id=source_message_id,
            target_session_uuid=target_session_uuid,
            target_message_uuid=target_message_uuid,
            processing_mode=processing_mode,
            processing_stage="deduplication",
            status="already_processed",
            skip_reason="duplicate_message_already_mapped",
            model_profile_uuid=model_target.get("model_profile_uuid"),
            provider_type=model_target.get("provider_type"),
            model_name=model_target.get("model_name"),
            input_content_hash=None,
            finished_at=utc_now(),
        )

    def process_next_batch(self, import_run_uuid: str, limit: int = 25) -> dict[str, Any]:
        run = self.connection.execute(
            "SELECT * FROM import_runs WHERE import_run_uuid = ?", (import_run_uuid,)
        ).fetchone()
        if run is None:
            raise ValueError("import run not found")
        progress = self.connection.execute(
            "SELECT * FROM import_progress WHERE import_run_uuid = ?", (import_run_uuid,)
        ).fetchone()
        if progress is not None and (progress["paused"] or progress["cancelled"]):
            return self.processing_report(import_run_uuid)
        pending = self.connection.execute(
            """
            SELECT *
            FROM import_message_processing_status
            WHERE import_run_uuid = ?
              AND processing_mode = 'full_memory_reconstruction'
              AND status = 'queued'
            ORDER BY created_at, status_uuid
            LIMIT ?
            """,
            (import_run_uuid, max(1, limit)),
        ).fetchall()
        for row in pending:
            control = self.connection.execute(
                "SELECT paused, cancelled FROM import_progress WHERE import_run_uuid = ?",
                (import_run_uuid,),
            ).fetchone()
            if control is not None and (control["paused"] or control["cancelled"]):
                break
            self._process_one(dict(row))
        self.finalize_if_terminal(import_run_uuid)
        return self.processing_report(import_run_uuid)

    def retry_failed(self, import_run_uuid: str) -> dict[str, Any]:
        now = utc_now()
        failed = self.connection.execute(
            """
            SELECT status_uuid, job_uuid
            FROM import_message_processing_status
            WHERE import_run_uuid = ? AND status = 'failed'
            """,
            (import_run_uuid,),
        ).fetchall()
        with self.connection:
            for row in failed:
                self.connection.execute(
                    """
                    UPDATE import_message_processing_status
                    SET status = 'queued', processing_stage = 'memory_extraction',
                        retry_count = retry_count + 1, error_sanitized = NULL,
                        updated_at = ?, finished_at = NULL
                    WHERE status_uuid = ?
                    """,
                    (now, row["status_uuid"]),
                )
                if row["job_uuid"]:
                    self.connection.execute(
                        """
                        UPDATE jobs
                        SET status = 'pending', attempts = 0, last_error = NULL,
                            last_error_sanitized = NULL, locked_by = NULL, locked_at = NULL,
                            updated_at = ?
                        WHERE job_uuid = ?
                        """,
                        (now, row["job_uuid"]),
                    )
            if failed:
                self.connection.execute(
                    """
                    UPDATE import_runs
                    SET status = 'processing', completed_at = NULL
                    WHERE import_run_uuid = ?
                    """,
                    (import_run_uuid,),
                )
        return {"retried": len(failed), **self.processing_report(import_run_uuid)}

    def statuses(self, import_run_uuid: str) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in self.connection.execute(
                """
                SELECT * FROM import_message_processing_status
                WHERE import_run_uuid = ?
                ORDER BY created_at, status_uuid
                """,
                (import_run_uuid,),
            )
        ]

    def processing_report(self, import_run_uuid: str) -> dict[str, Any]:
        statuses = self.statuses(import_run_uuid)
        counts = Counter(str(item["status"]) for item in statuses)
        skip_reasons = Counter(
            str(item["skip_reason"])
            for item in statuses
            if item.get("skip_reason") and item["status"] == "skipped"
        )
        run = self.connection.execute(
            "SELECT * FROM import_runs WHERE import_run_uuid = ?", (import_run_uuid,)
        ).fetchone()
        message_uuids = [
            str(item["target_message_uuid"])
            for item in statuses
            if item.get("target_message_uuid")
        ]
        metrics = self._artifact_metrics(import_run_uuid, message_uuids)
        return {
            "import_run_uuid": import_run_uuid,
            "imported_conversations": int(run["imported_conversations"] or 0) if run else 0,
            "imported_messages": int(run["imported_messages"] or 0) if run else 0,
            "failed_messages": counts["failed"],
            "skipped_messages": counts["skipped"],
            "skip_reasons": dict(skip_reasons),
            "processing_jobs_total": len(statuses),
            "processing_jobs_pending": counts["pending"],
            "processing_jobs_queued": counts["queued"],
            "processing_jobs_running": counts["running"],
            "processing_jobs_succeeded": counts["succeeded"],
            "processing_jobs_failed": counts["failed"],
            "processing_jobs_skipped": counts["skipped"],
            "processing_jobs_already_processed": counts["already_processed"],
            "terminal": all(
                str(item["status"]) in TERMINAL_STATUSES for item in statuses
            ),
            **metrics,
        }

    def finalize_if_terminal(self, import_run_uuid: str) -> bool:
        report = self.processing_report(import_run_uuid)
        run = self.connection.execute(
            "SELECT status FROM import_runs WHERE import_run_uuid = ?", (import_run_uuid,)
        ).fetchone()
        if run is not None and run["status"] in {"paused", "cancelled"}:
            return False
        if not report["terminal"]:
            with self.connection:
                self.connection.execute(
                    "UPDATE import_runs SET status = 'processing' WHERE import_run_uuid = ?",
                    (import_run_uuid,),
                )
            return False
        with self.connection:
            self.connection.execute(
                """
                UPDATE import_runs
                SET status = 'fully_reconstructed', completed_at = ?
                WHERE import_run_uuid = ?
                """,
                (utc_now(), import_run_uuid),
            )
        return True

    def _process_one(self, status_row: dict[str, Any]) -> None:
        now = utc_now()
        job_uuid = status_row.get("job_uuid")
        with self.connection:
            self.connection.execute(
                """
                UPDATE import_message_processing_status
                SET status = 'running', updated_at = ?
                WHERE status_uuid = ? AND status = 'queued'
                """,
                (now, status_row["status_uuid"]),
            )
            if job_uuid:
                self.connection.execute(
                    """
                    UPDATE jobs
                    SET status = 'running', attempts = attempts + 1, locked_by = 'import-processor',
                        locked_at = ?, updated_at = ?
                    WHERE job_uuid = ?
                    """,
                    (now, now, job_uuid),
                )
        try:
            result = MemoryWorkerPipeline(self.connection, self.settings).process_message(
                str(status_row["target_message_uuid"]),
                import_run_uuid=str(status_row["import_run_uuid"]),
                job_uuid=str(job_uuid) if job_uuid else None,
                model_target={
                    "model_profile_uuid": status_row.get("model_profile_uuid"),
                    "provider_type": status_row.get("provider_type") or "deterministic",
                    "model_name": status_row.get("model_name") or "deterministic_extraction",
                },
            )
            processing_run_uuid = str(result["processing_run_uuid"])
            execution = self.connection.execute(
                """
                SELECT prompt_execution_uuid
                FROM prompt_execution_runs
                WHERE import_run_uuid = ? AND message_uuid = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (status_row["import_run_uuid"], status_row["target_message_uuid"]),
            ).fetchone()
            finished = utc_now()
            with self.connection:
                self.connection.execute(
                    """
                    UPDATE import_message_processing_status
                    SET status = 'succeeded', processing_stage = 'complete',
                        memory_processing_run_uuid = ?, prompt_execution_uuid = ?,
                        error_sanitized = NULL, updated_at = ?, finished_at = ?
                    WHERE status_uuid = ?
                    """,
                    (
                        processing_run_uuid,
                        execution["prompt_execution_uuid"] if execution else None,
                        finished,
                        finished,
                        status_row["status_uuid"],
                    ),
                )
                if job_uuid:
                    self.connection.execute(
                        """
                        UPDATE jobs
                        SET status = 'succeeded', locked_by = NULL, locked_at = NULL, updated_at = ?
                        WHERE job_uuid = ?
                        """,
                        (finished, job_uuid),
                    )
        except Exception as error:
            sanitized = f"{type(error).__name__}: {str(error)}"[:240]
            finished = utc_now()
            run = self.connection.execute(
                """
                SELECT processing_run_uuid FROM memory_processing_runs
                WHERE message_uuid = ? ORDER BY created_at DESC LIMIT 1
                """,
                (status_row["target_message_uuid"],),
            ).fetchone()
            if run is not None:
                with self.connection:
                    self.connection.execute(
                        """
                        UPDATE memory_processing_runs
                        SET status = 'failed', completed_at = ?, error_text = ?
                        WHERE processing_run_uuid = ?
                        """,
                        (finished, sanitized, run["processing_run_uuid"]),
                    )
            self.model_control.record_usage_event(
                UsageEventCreate(
                    role=ModelRole.MEMORY_EXTRACTION,
                    stage="import_memory_reconstruction",
                    model_profile_uuid=status_row.get("model_profile_uuid"),
                    session_uuid=status_row.get("target_session_uuid"),
                    message_uuid=status_row.get("target_message_uuid"),
                    import_run_uuid=status_row.get("import_run_uuid"),
                    job_uuid=job_uuid,
                    status="error",
                    error_class=type(error).__name__,
                    error_message_sanitized=sanitized,
                )
            )
            with self.connection:
                self.connection.execute(
                    """
                    UPDATE import_message_processing_status
                    SET status = 'failed', error_sanitized = ?, updated_at = ?, finished_at = ?
                    WHERE status_uuid = ?
                    """,
                    (sanitized, finished, finished, status_row["status_uuid"]),
                )
                if job_uuid:
                    self.connection.execute(
                        """
                        UPDATE jobs
                        SET status = 'failed', last_error = ?, last_error_sanitized = ?,
                            locked_by = NULL, locked_at = NULL, updated_at = ?
                        WHERE job_uuid = ?
                        """,
                        (sanitized, sanitized, finished, job_uuid),
                    )

    def _upsert_status(self, **values: Any) -> dict[str, Any]:
        existing = self.connection.execute(
            """
            SELECT * FROM import_message_processing_status
            WHERE import_run_uuid = ? AND source_platform = ?
              AND COALESCE(source_conversation_id, '') = COALESCE(?, '')
              AND COALESCE(source_message_id, '') = COALESCE(?, '')
              AND processing_mode = ?
            """,
            (
                values["import_run_uuid"],
                values["source_platform"],
                values.get("source_conversation_id"),
                values.get("source_message_id"),
                values["processing_mode"],
            ),
        ).fetchone()
        if existing is not None:
            return dict(existing)
        now = utc_now()
        row = {
            "status_uuid": new_uuid(),
            "import_run_uuid": values["import_run_uuid"],
            "source_platform": values["source_platform"],
            "source_conversation_id": values.get("source_conversation_id"),
            "source_message_id": values.get("source_message_id"),
            "target_session_uuid": values.get("target_session_uuid"),
            "target_message_uuid": values.get("target_message_uuid"),
            "processing_mode": values["processing_mode"],
            "processing_stage": values["processing_stage"],
            "status": values["status"],
            "skip_reason": values.get("skip_reason"),
            "job_uuid": values.get("job_uuid"),
            "memory_processing_run_uuid": values.get("memory_processing_run_uuid"),
            "prompt_execution_uuid": values.get("prompt_execution_uuid"),
            "model_profile_uuid": values.get("model_profile_uuid"),
            "provider_type": values.get("provider_type"),
            "model_name": values.get("model_name"),
            "input_content_hash": values.get("input_content_hash"),
            "retry_count": 0,
            "error_sanitized": values.get("error_sanitized"),
            "created_at": now,
            "updated_at": now,
            "finished_at": values.get("finished_at"),
            "schema_version": 1,
        }
        columns = ", ".join(row)
        placeholders = ", ".join("?" for _ in row)
        with self.connection:
            self.connection.execute(
                f"INSERT INTO import_message_processing_status ({columns}) VALUES ({placeholders})",
                tuple(row.values()),
            )
        return row

    def _artifact_metrics(
        self, import_run_uuid: str, message_uuids: list[str]
    ) -> dict[str, int]:
        if not message_uuids:
            return {
                "processed_messages": 0,
                "memory_candidates_created": 0,
                "memory_versions_created": 0,
                "prompt_execution_runs": 0,
                "model_usage_events": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "graph_projection_outbox_events": 0,
            }
        placeholders = ",".join("?" for _ in message_uuids)
        candidates = int(
            self.connection.execute(
                f"""
                SELECT COUNT(*) FROM memory_candidates mc
                JOIN text_units tu ON tu.text_unit_uuid = mc.text_unit_uuid
                WHERE tu.message_uuid IN ({placeholders})
                """,
                tuple(message_uuids),
            ).fetchone()[0]
        )
        versions = int(
            self.connection.execute(
                f"""
                SELECT COUNT(*) FROM memory_versions mv
                JOIN memory_candidates mc ON mc.candidate_uuid = mv.source_candidate_uuid
                JOIN text_units tu ON tu.text_unit_uuid = mc.text_unit_uuid
                WHERE tu.message_uuid IN ({placeholders})
                """,
                tuple(message_uuids),
            ).fetchone()[0]
        )
        prompt = self.connection.execute(
            """
            SELECT COUNT(*) FROM prompt_execution_runs WHERE import_run_uuid = ?
            """,
            (import_run_uuid,),
        ).fetchone()[0]
        usage = self.connection.execute(
            """
            SELECT COUNT(*), COALESCE(SUM(input_tokens), 0), COALESCE(SUM(output_tokens), 0)
            FROM model_usage_events WHERE import_run_uuid = ?
            """,
            (import_run_uuid,),
        ).fetchone()
        outbox = self.connection.execute(
            f"""
            SELECT COUNT(DISTINCT g.outbox_uuid)
            FROM graph_projection_outbox g
            WHERE g.aggregate_uuid IN (
                SELECT mv.memory_uuid
                FROM memory_versions mv
                JOIN memory_candidates mc ON mc.candidate_uuid = mv.source_candidate_uuid
                JOIN text_units tu ON tu.text_unit_uuid = mc.text_unit_uuid
                WHERE tu.message_uuid IN ({placeholders})
            ) OR g.aggregate_uuid IN (
                SELECT analysis_run_uuid
                FROM jakobson_analysis_runs
                WHERE message_uuid IN ({placeholders})
            )
            """,
            tuple(message_uuids) + tuple(message_uuids),
        ).fetchone()[0]
        return {
            "processed_messages": int(
                self.connection.execute(
                    """
                    SELECT COUNT(*) FROM import_message_processing_status
                    WHERE import_run_uuid = ? AND status IN ('succeeded', 'already_processed')
                    """,
                    (import_run_uuid,),
                ).fetchone()[0]
            ),
            "memory_candidates_created": candidates,
            "memory_versions_created": versions,
            "prompt_execution_runs": int(prompt),
            "model_usage_events": int(usage[0]),
            "input_tokens": int(usage[1]),
            "output_tokens": int(usage[2]),
            "graph_projection_outbox_events": int(outbox),
        }
