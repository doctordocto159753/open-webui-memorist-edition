from __future__ import annotations

import os
import sqlite3
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any

from memcore.config import Settings
from memcore.imports.reconstruction.content_parts import visible_text
from memcore.imports.reconstruction.models import ImportedMessageNode
from memcore.memory_worker.contracts import PIPELINE_VERSION, PROMPT_BUNDLE_VERSION
from memcore.memory_worker.pipeline import MemoryWorkerPipeline
from memcore.model_control.repository import ModelControlRepository
from memcore.model_control.schemas import UsageEventCreate
from memcore.model_control.security import sanitize_error_message
from memcore.models import ModelRole, new_uuid, utc_now
from memcore.repositories import JobRepository
from memcore.repositories.memory_worker import canonical_text_hash

PROCESSING_MODES = {"none", "extract_candidates", "full_memory_reconstruction"}
TERMINAL_STATUSES = {"succeeded", "skipped", "already_processed", "failed", "cancelled"}
ACTIVE_STATUSES = {"queued", "running"}


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
        warnings: list[str] = []
        first_fallback: dict[str, Any] | None = None
        for role in (ModelRole.IMPORT_RECONSTRUCTION, ModelRole.MEMORY_EXTRACTION):
            candidate = self._model_target_for_role(
                role,
                workspace_uuid=workspace_uuid,
                project_uuid=project_uuid,
            )
            if candidate is None:
                warnings.append(f"no {role.value} default is configured")
                continue
            warnings.extend(candidate["warnings"])
            if not candidate["deterministic_fallback"]:
                candidate["fallback_role"] = (
                    None if role is ModelRole.IMPORT_RECONSTRUCTION else role.value
                )
                return candidate
            if first_fallback is None:
                first_fallback = candidate

        warnings.append("deterministic fallback will be used")
        fallback_enabled = (
            bool(first_fallback.get("profile_enabled")) if first_fallback is not None else True
        )
        fallback_privacy = (
            bool(first_fallback.get("privacy_acknowledged")) if first_fallback is not None else True
        )
        fallback_secret = (
            bool(first_fallback.get("secret_configured")) if first_fallback is not None else True
        )
        return {
            "role": ModelRole.MEMORY_EXTRACTION.value,
            "resolved_role": "deterministic",
            "model_profile_uuid": None,
            "provider_type": "deterministic",
            "model_name": "deterministic_extraction",
            "deterministic_fallback": True,
            "profile_enabled": fallback_enabled,
            "privacy_acknowledged": fallback_privacy,
            "secret_configured": fallback_secret,
            "pipeline_version": PIPELINE_VERSION,
            "prompt_bundle_version": PROMPT_BUNDLE_VERSION,
            "warnings": warnings,
        }

    def _model_target_for_role(
        self,
        role: ModelRole,
        *,
        workspace_uuid: str | None,
        project_uuid: str | None,
    ) -> dict[str, Any] | None:
        profile = self.model_control.resolve_default(
            role,
            workspace_uuid=workspace_uuid,
            project_uuid=project_uuid,
        )
        if profile is None:
            return None

        warnings: list[str] = []
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
            warnings.append(f"configured {role.value} profile is disabled")
        if not privacy_acknowledged:
            warnings.append(f"configured {role.value} profile lacks privacy acknowledgement")
        if not secret_configured:
            warnings.append("required provider secret environment variable is missing")
        if health is not None and health["status"] not in {"ok", "healthy"}:
            warnings.append(
                f"latest {role.value} provider health is {health['status']}; "
                "runtime failures remain retryable"
            )
        usable = enabled and privacy_acknowledged and secret_configured
        return {
            "role": role.value,
            "resolved_role": role.value,
            "model_profile_uuid": profile.get("model_profile_uuid") if usable else None,
            "provider_type": (
                (profile.get("provider_type") or profile.get("provider"))
                if usable
                else "deterministic"
            ),
            "model_name": profile.get("model_name") if usable else "deterministic_extraction",
            "deterministic_fallback": not usable,
            "profile_enabled": enabled,
            "privacy_acknowledged": privacy_acknowledged,
            "secret_configured": secret_configured,
            "latest_health": dict(health) if health is not None else None,
            "pipeline_version": PIPELINE_VERSION,
            "prompt_bundle_version": PROMPT_BUNDLE_VERSION,
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
        input_content_hash = canonical_text_hash(text)
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
            "input_content_hash": input_content_hash,
            "pipeline_version": PIPELINE_VERSION,
            "prompt_bundle_version": PROMPT_BUNDLE_VERSION,
            "model_role": model_target.get("role") or ModelRole.MEMORY_EXTRACTION.value,
        }
        common["processing_identity"] = self._processing_identity(
            target_message_uuid=target_message_uuid,
            processing_mode=processing_mode,
            input_content_hash=input_content_hash,
            model_target=model_target,
        )
        if not eligible:
            return self._upsert_status(
                **common,
                processing_stage="eligibility",
                status="skipped",
                skip_reason=skip_reason,
                processing_decision=skip_reason,
                finished_at=utc_now(),
            )
        processing_state = self.determine_processing_state(
            target_message_uuid=target_message_uuid,
            input_content_hash=str(common["input_content_hash"]),
            model_target=model_target,
        )
        if processing_state["decision"] == "already_processed":
            return self._upsert_status(
                **common,
                processing_stage="complete",
                status="already_processed",
                processing_decision="already_processed",
                memory_processing_run_uuid=processing_state.get("processing_run_uuid"),
                finished_at=utc_now(),
            )
        if processing_state["decision"] == "currently_active":
            return self._upsert_status(
                **common,
                processing_stage="memory_extraction",
                status=str(processing_state["status"]),
                processing_decision="currently_active",
                memory_processing_run_uuid=processing_state.get("processing_run_uuid"),
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
            "model_role": common["model_role"],
            "model_profile_uuid": model_target.get("model_profile_uuid"),
            "processing_identity": common["processing_identity"],
            "pipeline_version": PIPELINE_VERSION,
            "prompt_bundle_version": PROMPT_BUNDLE_VERSION,
        }
        job = JobRepository(self.connection).enqueue_job_once(
            job_type,
            payload,
            priority=30,
            max_attempts=max(
                1,
                int(getattr(self.settings, "import_reconstruction_max_attempts", 5)),
            ),
            idempotency_key=f"{job_type}:{common['processing_identity']}",
        )
        self.model_control.record_usage_event(
            UsageEventCreate(
                role=ModelRole(str(common["model_role"])),
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
            processing_decision=str(processing_state["decision"]),
            job_uuid=job.job_uuid,
        )

    def determine_processing_state(
        self,
        *,
        target_message_uuid: str,
        input_content_hash: str,
        model_target: dict[str, Any],
    ) -> dict[str, Any]:
        rows = self.connection.execute(
            """
            SELECT *
            FROM memory_processing_runs
            WHERE message_uuid = ?
            ORDER BY created_at DESC
            """,
            (target_message_uuid,),
        ).fetchall()
        if not rows:
            return {"decision": "imported_but_unprocessed"}

        model_profile_uuid = model_target.get("model_profile_uuid")
        for row in rows:
            current_contract = (
                row["pipeline_version"] == PIPELINE_VERSION
                and row["prompt_bundle_version"] == PROMPT_BUNDLE_VERSION
                and row["input_content_hash"] == input_content_hash
                and _nullable_equal(row["model_profile_uuid"], model_profile_uuid)
            )
            if not current_contract:
                continue
            if row["status"] == "succeeded":
                return {
                    "decision": "already_processed",
                    "status": "already_processed",
                    "processing_run_uuid": row["processing_run_uuid"],
                }
            if row["status"] in {"running", "pending"}:
                return {
                    "decision": "currently_active",
                    "status": "running" if row["status"] == "running" else "queued",
                    "processing_run_uuid": row["processing_run_uuid"],
                }
            if row["status"] == "failed":
                return {
                    "decision": "processing_failed",
                    "processing_run_uuid": row["processing_run_uuid"],
                }

        if any(row["input_content_hash"] != input_content_hash for row in rows):
            return {"decision": "processing_required"}
        return {"decision": "processing_stale"}

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
            status="queued",
            processing_decision="already_imported",
            skip_reason=None,
            model_profile_uuid=model_target.get("model_profile_uuid"),
            provider_type=model_target.get("provider_type"),
            model_name=model_target.get("model_name"),
            input_content_hash=None,
            pipeline_version=PIPELINE_VERSION,
            prompt_bundle_version=PROMPT_BUNDLE_VERSION,
            model_role=model_target.get("role") or ModelRole.MEMORY_EXTRACTION.value,
            processing_identity=None,
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
        self.recover_expired_leases(import_run_uuid)
        claimed = self.claim_next_batch(import_run_uuid, max(1, limit))
        for row in claimed:
            control = self.connection.execute(
                "SELECT paused, cancelled FROM import_progress WHERE import_run_uuid = ?",
                (import_run_uuid,),
            ).fetchone()
            if control is not None and (control["paused"] or control["cancelled"]):
                break
            self._process_claimed(dict(row))
        self.finalize_if_terminal(import_run_uuid)
        return self.processing_report(import_run_uuid)

    def claim_next_batch(self, import_run_uuid: str, limit: int = 25) -> list[dict[str, Any]]:
        now = utc_now()
        lease_expires_at = _iso_after_seconds(
            int(getattr(self.settings, "import_reconstruction_lease_seconds", 300))
        )
        worker_id = "import-reconstruction-worker"
        with self.connection:
            rows = self.connection.execute(
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
            claimed: list[dict[str, Any]] = []
            for row in rows:
                updated = self.connection.execute(
                    """
                    UPDATE import_message_processing_status
                    SET status = 'running', processing_stage = 'memory_extraction',
                        lease_owner = ?, lease_expires_at = ?, attempt_started_at = ?,
                        updated_at = ?
                    WHERE status_uuid = ? AND status = 'queued'
                    """,
                    (worker_id, lease_expires_at, now, now, row["status_uuid"]),
                )
                if updated.rowcount != 1:
                    continue
                if row["job_uuid"]:
                    self.connection.execute(
                        """
                        UPDATE jobs
                        SET status = 'running', attempts = attempts + 1,
                            locked_by = ?, locked_at = ?, updated_at = ?
                        WHERE job_uuid = ?
                        """,
                        (worker_id, now, now, row["job_uuid"]),
                    )
                claimed.append(
                    dict(
                        self.connection.execute(
                            """
                            SELECT * FROM import_message_processing_status
                            WHERE status_uuid = ?
                            """,
                            (row["status_uuid"],),
                        ).fetchone()
                    )
                )
        return claimed

    def recover_expired_leases(self, import_run_uuid: str) -> int:
        now = utc_now()
        with self.connection:
            cursor = self.connection.execute(
                """
                UPDATE import_message_processing_status
                SET status = 'queued', lease_owner = NULL, lease_expires_at = NULL,
                    processing_stage = 'memory_extraction', updated_at = ?
                WHERE import_run_uuid = ?
                  AND status = 'running'
                  AND lease_expires_at IS NOT NULL
                  AND lease_expires_at < ?
                """,
                (now, import_run_uuid, now),
            )
        return int(cursor.rowcount or 0)

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
                        lease_owner = NULL, lease_expires_at = NULL,
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
            str(item["target_message_uuid"]) for item in statuses if item.get("target_message_uuid")
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
            "processing_jobs_cancelled": counts["cancelled"],
            "retries": sum(int(item.get("retry_count") or 0) for item in statuses),
            "terminal": all(str(item["status"]) in TERMINAL_STATUSES for item in statuses)
            if statuses
            else False,
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
        final_status = (
            "completed_with_failures"
            if report["processing_jobs_failed"] > 0
            else "fully_reconstructed"
        )
        with self.connection:
            self.connection.execute(
                """
                UPDATE import_runs
                SET status = ?, completed_at = ?
                WHERE import_run_uuid = ?
                """,
                (final_status, utc_now(), import_run_uuid),
            )
        return True

    def _process_claimed(self, status_row: dict[str, Any]) -> None:
        job_uuid = status_row.get("job_uuid")
        try:
            result = MemoryWorkerPipeline(self.connection, self.settings).process_message(
                str(status_row["target_message_uuid"]),
                import_run_uuid=str(status_row["import_run_uuid"]),
                job_uuid=str(job_uuid) if job_uuid else None,
                model_target={
                    "model_profile_uuid": status_row.get("model_profile_uuid"),
                    "provider_type": status_row.get("provider_type") or "deterministic",
                    "model_name": status_row.get("model_name") or "deterministic_extraction",
                    "role": status_row.get("model_role") or ModelRole.MEMORY_EXTRACTION.value,
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
                        error_sanitized = NULL, lease_owner = NULL, lease_expires_at = NULL,
                        updated_at = ?, finished_at = ?
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
            sanitized = sanitize_error_message(f"{type(error).__name__}: {error}")
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
                    role=ModelRole(
                        str(status_row.get("model_role") or ModelRole.MEMORY_EXTRACTION.value)
                    ),
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
                    SET status = 'failed', error_sanitized = ?,
                        lease_owner = NULL, lease_expires_at = NULL,
                        updated_at = ?, finished_at = ?
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
            mutable_statuses = {"queued", "running", "failed", "cancelled"}
            if str(existing["status"]) in mutable_statuses or values["status"] in ACTIVE_STATUSES:
                updates = {
                    "target_session_uuid": values.get("target_session_uuid"),
                    "target_message_uuid": values.get("target_message_uuid"),
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
                    "processing_decision": values.get("processing_decision"),
                    "pipeline_version": values.get("pipeline_version"),
                    "prompt_bundle_version": values.get("prompt_bundle_version"),
                    "model_role": values.get("model_role"),
                    "processing_identity": values.get("processing_identity"),
                    "error_sanitized": values.get("error_sanitized"),
                    "updated_at": utc_now(),
                    "finished_at": values.get("finished_at"),
                }
                assignments = ", ".join(f"{column} = ?" for column in updates)
                with self.connection:
                    self.connection.execute(
                        f"""
                        UPDATE import_message_processing_status
                        SET {assignments}
                        WHERE status_uuid = ?
                        """,
                        tuple(updates.values()) + (existing["status_uuid"],),
                    )
                refreshed = self.connection.execute(
                    "SELECT * FROM import_message_processing_status WHERE status_uuid = ?",
                    (existing["status_uuid"],),
                ).fetchone()
                return dict(refreshed)
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
            "processing_decision": values.get("processing_decision"),
            "pipeline_version": values.get("pipeline_version"),
            "prompt_bundle_version": values.get("prompt_bundle_version"),
            "model_role": values.get("model_role"),
            "processing_identity": values.get("processing_identity"),
            "lease_owner": values.get("lease_owner"),
            "lease_expires_at": values.get("lease_expires_at"),
            "attempt_started_at": values.get("attempt_started_at"),
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

    def _artifact_metrics(self, import_run_uuid: str, message_uuids: list[str]) -> dict[str, int]:
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

    def _processing_identity(
        self,
        *,
        target_message_uuid: str,
        processing_mode: str,
        input_content_hash: str,
        model_target: dict[str, Any],
    ) -> str:
        identity_payload = {
            "target_message_uuid": target_message_uuid,
            "processing_mode": processing_mode,
            "pipeline_version": PIPELINE_VERSION,
            "prompt_bundle_version": PROMPT_BUNDLE_VERSION,
            "input_content_hash": input_content_hash,
            "model_role": model_target.get("role") or ModelRole.MEMORY_EXTRACTION.value,
            "model_profile_uuid": model_target.get("model_profile_uuid"),
            "provider_type": model_target.get("provider_type"),
            "model_name": model_target.get("model_name"),
        }
        return canonical_text_hash(repr(sorted(identity_payload.items())))


def _nullable_equal(left: object, right: object) -> bool:
    return (None if left is None else str(left)) == (None if right is None else str(right))


def _iso_after_seconds(seconds: int) -> str:
    return (
        (datetime.now(UTC) + timedelta(seconds=max(1, seconds)))
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )
