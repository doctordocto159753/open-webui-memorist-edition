from __future__ import annotations

import os
import socket
import threading
import uuid
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any, cast

from memcore.config import Settings
from memcore.imports.reconstruction.content_parts import visible_text
from memcore.imports.reconstruction.models import ImportedMessageNode
from memcore.memory_worker.identity import build_processing_identity
from memcore.memory_worker.pipeline import MemoryWorkerPipeline
from memcore.memory_worker.postgres.pipeline import PostgresMemoryWorkerPipeline
from memcore.model_control.postgres_repository import PostgresModelControlRepository
from memcore.model_control.repository import ModelControlRepository
from memcore.model_control.schemas import UsageEventCreate
from memcore.model_control.security import sanitize_error_message
from memcore.models import ModelRole, new_uuid, utc_now
from memcore.repositories import JobRepository
from memcore.storage.sqlite import NestedTransactionConnection

PROCESSING_MODES = {"none", "extract_candidates", "full_memory_reconstruction"}
TERMINAL_STATUSES = {"succeeded", "skipped", "already_processed", "failed", "cancelled"}


class ImportLeaseLost(RuntimeError):
    """Raised when a processing attempt no longer owns its lease fence."""


class ImportMessageProcessor:
    def __init__(self, connection: Any, settings: Settings) -> None:
        self.connection = connection
        self.settings = settings
        self.model_control = _model_control_repository(connection, settings)

    def validate_mode(self, processing_mode: str) -> str:
        if processing_mode not in PROCESSING_MODES:
            allowed = ", ".join(sorted(PROCESSING_MODES))
            raise ValueError(f"unsupported processing_mode; expected one of: {allowed}")
        return processing_mode

    def model_target(
        self, workspace_uuid: str | None = None, project_uuid: str | None = None
    ) -> dict[str, Any]:
        warnings: list[str] = []
        for role in (ModelRole.IMPORT_RECONSTRUCTION, ModelRole.MEMORY_EXTRACTION):
            profile = self.model_control.resolve_default(
                role, workspace_uuid=workspace_uuid, project_uuid=project_uuid
            )
            target = self._usable_model_target(role, profile, warnings)
            if target is not None:
                return target
        if not self.settings.import_reconstruction_allow_deterministic_fallback:
            raise RuntimeError(
                "no configured import reconstruction profile and deterministic fallback is disabled"
            )
        return {
            "role": ModelRole.IMPORT_RECONSTRUCTION.value,
            "model_role": ModelRole.IMPORT_RECONSTRUCTION.value,
            "model_profile_uuid": None,
            "provider_type": "deterministic",
            "model_name": "deterministic_extraction",
            "deterministic_fallback": True,
            "deterministic_fallback_policy": "explicitly_allowed",
            "profile_enabled": True,
            "privacy_acknowledged": True,
            "secret_configured": True,
            "warnings": warnings,
        }

    def _usable_model_target(
        self, role: ModelRole, profile: dict[str, Any] | None, warnings: list[str]
    ) -> dict[str, Any] | None:
        if profile is None:
            return None
        enabled = bool(profile.get("is_enabled", True))
        requires_ack = bool(profile.get("requires_privacy_acknowledgement", False))
        privacy_acknowledged = bool(profile.get("privacy_acknowledged_at")) or not requires_ack
        internal_profile = self.model_control.get_profile(str(profile["model_profile_uuid"]))
        secret_env = internal_profile.secret_env_var_name if internal_profile is not None else None
        secret_configured = not secret_env or bool(os.getenv(str(secret_env)))
        provider_type = str(profile.get("provider_type") or profile.get("provider") or "")
        supported = provider_type in {"deterministic", "openai_compatible", "openai_compatible_llm"}
        model_name_configured = bool(str(profile.get("model_name") or "").strip())
        endpoint_configured = provider_type == "deterministic" or bool(
            str(profile.get("endpoint_url") or "").strip()
        )
        structured_output_supported = provider_type == "deterministic" or bool(
            profile.get("supports_structured_output") or profile.get("supports_json_mode")
        )
        prefix = role.value
        if not enabled:
            warnings.append(f"configured {prefix} profile is disabled")
        if not privacy_acknowledged:
            warnings.append(f"configured {prefix} profile lacks privacy acknowledgement")
        if not secret_configured:
            warnings.append(
                f"configured {prefix} profile is missing required secret environment variable"
            )
        if not supported:
            warnings.append(f"configured {prefix} provider type is unsupported")
        if not model_name_configured:
            warnings.append(f"configured {prefix} profile is missing a model name")
        if not endpoint_configured:
            warnings.append(f"configured {prefix} profile is missing an endpoint")
        if not structured_output_supported:
            warnings.append(f"configured {prefix} profile lacks structured output capability")
        if not (
            enabled
            and privacy_acknowledged
            and secret_configured
            and supported
            and model_name_configured
            and endpoint_configured
            and structured_output_supported
        ):
            return None
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
        return {
            "role": role.value,
            "model_role": role.value,
            "model_profile_uuid": profile.get("model_profile_uuid"),
            "provider_type": provider_type,
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
        identity = build_processing_identity(
            target_message_uuid=target_message_uuid,
            raw_text=text,
            model_target=model_target,
            model_role=str(model_target.get("role") or ModelRole.IMPORT_RECONSTRUCTION.value),
        )
        common = {
            "import_run_uuid": import_run_uuid,
            "source_platform": source_platform,
            "source_conversation_id": source_conversation_id,
            "source_message_id": source_message_id,
            "target_session_uuid": target_session_uuid,
            "target_message_uuid": target_message_uuid,
            "processing_mode": processing_mode,
            "model_role": identity.model_role,
            "model_profile_uuid": model_target.get("model_profile_uuid"),
            "provider_type": model_target.get("provider_type"),
            "model_name": model_target.get("model_name"),
            "pipeline_version": identity.pipeline_version,
            "prompt_bundle_version": identity.prompt_bundle_version,
            "prompt_id": identity.prompt_id,
            "prompt_version": identity.prompt_version,
            "input_content_hash": identity.input_content_hash,
            "processing_identity_hash": identity.identity_hash,
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
              AND pipeline_version = ? AND prompt_bundle_version = ?
              AND COALESCE(model_role, '') = COALESCE(?, '')
              AND COALESCE(model_profile_uuid, '') = COALESCE(?, '')
              AND COALESCE(provider_type, '') = COALESCE(?, '')
              AND COALESCE(model_name, '') = COALESCE(?, '')
              AND COALESCE(prompt_id, '') = COALESCE(?, '')
              AND COALESCE(prompt_version, '') = COALESCE(?, '')
              AND COALESCE(processing_identity_hash, '') = COALESCE(?, '')
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (
                target_message_uuid,
                identity.input_content_hash,
                identity.pipeline_version,
                identity.prompt_bundle_version,
                identity.model_role,
                identity.model_profile_uuid,
                identity.provider_type,
                model_target.get("model_name"),
                identity.prompt_id,
                identity.prompt_version,
                identity.identity_hash,
            ),
        ).fetchone()
        if already_processed is not None:
            return self._upsert_status(
                **common,
                processing_stage="complete",
                status="already_processed",
                memory_processing_run_uuid=already_processed["processing_run_uuid"],
                finished_at=utc_now(),
            )

        active = self.connection.execute(
            """
            SELECT * FROM import_message_processing_status
            WHERE processing_identity_hash = ? AND status IN ('queued', 'running')
            ORDER BY created_at LIMIT 1
            """,
            (identity.identity_hash,),
        ).fetchone()
        if active is not None:
            return self._upsert_status(
                **common,
                processing_stage=str(active["processing_stage"]),
                status=str(active["status"]),
                job_uuid=active["job_uuid"],
                memory_processing_run_uuid=active["memory_processing_run_uuid"],
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
            "model_role": identity.model_role,
            "model_profile_uuid": model_target.get("model_profile_uuid"),
            "processing_identity_hash": identity.identity_hash,
        }
        job = JobRepository(self.connection).enqueue_job_once(
            job_type, payload, priority=30, max_attempts=1
        )
        self.model_control.record_usage_event(
            UsageEventCreate(
                role=ModelRole(identity.model_role),
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
        for _ in range(max(1, limit)):
            control = self.connection.execute(
                "SELECT paused, cancelled FROM import_progress WHERE import_run_uuid = ?",
                (import_run_uuid,),
            ).fetchone()
            if control is not None and (control["paused"] or control["cancelled"]):
                break
            claimed = self.claim_next(_worker_id(), 900, import_run_uuid)
            if claimed is None:
                break
            self._process_one(dict(claimed))
        self.finalize_if_terminal(import_run_uuid)
        return self.processing_report(import_run_uuid)

    def claim_next(
        self, worker_id: str, lease_seconds: int, import_run_uuid: str | None = None
    ) -> dict[str, Any] | None:
        if self.settings.runtime_profile == "full" and self.settings.canonical_store == "postgres":
            return self._claim_next_postgres(worker_id, lease_seconds, import_run_uuid)
        now = utc_now()
        lease_expires = _add_seconds(now, lease_seconds)
        attempt_uuid = new_uuid()
        params: list[Any] = []
        import_filter = ""
        if import_run_uuid is not None:
            import_filter = "AND status.import_run_uuid = ?"
            params.append(import_run_uuid)
        params.append(now)
        row = self.connection.execute(
            f"""
            SELECT status.*
            FROM import_message_processing_status status
            JOIN import_runs run ON run.import_run_uuid = status.import_run_uuid
            LEFT JOIN import_progress progress
              ON progress.import_run_uuid = status.import_run_uuid
            WHERE status.processing_mode = 'full_memory_reconstruction'
              AND status.status = 'queued'
              {import_filter}
              AND (status.run_after IS NULL OR status.run_after <= ?)
              AND run.status = 'processing'
              AND COALESCE(progress.paused, 0) = 0
              AND COALESCE(progress.cancelled, 0) = 0
            ORDER BY status.created_at, status.status_uuid
            LIMIT 1
            """,
            tuple(params),
        ).fetchone()
        if row is None:
            return None
        with self.connection:
            updated = self.connection.execute(
                """
                UPDATE import_message_processing_status
                SET status = 'running', lease_owner = ?, lease_acquired_at = ?,
                    lease_expires_at = ?, heartbeat_at = ?, attempt_started_at = ?,
                    processing_attempt_uuid = ?,
                    last_transition_at = ?, updated_at = ?
                WHERE status_uuid = ? AND status = 'queued'
                """,
                (
                    worker_id,
                    now,
                    lease_expires,
                    now,
                    now,
                    attempt_uuid,
                    now,
                    now,
                    row["status_uuid"],
                ),
            ).rowcount
        if not updated:
            return None
        claimed = self.connection.execute(
            "SELECT * FROM import_message_processing_status WHERE status_uuid = ?",
            (row["status_uuid"],),
        ).fetchone()
        return dict(claimed) if claimed is not None else None

    def _claim_next_postgres(
        self, worker_id: str, lease_seconds: int, import_run_uuid: str | None = None
    ) -> dict[str, Any] | None:
        import_filter = "AND status.import_run_uuid = ?" if import_run_uuid is not None else ""
        params: list[Any] = []
        if import_run_uuid is not None:
            params.append(import_run_uuid)
        attempt_uuid = new_uuid()
        params.extend([worker_id, attempt_uuid, lease_seconds])
        rows = self.connection.execute(
            f"""
            WITH candidates AS (
                SELECT status.status_uuid
                FROM import_message_processing_status status
                JOIN import_runs run ON run.import_run_uuid = status.import_run_uuid
                LEFT JOIN import_progress progress
                  ON progress.import_run_uuid = status.import_run_uuid
                WHERE status.processing_mode = 'full_memory_reconstruction'
                  AND status.status = 'queued'
                  {import_filter}
                  AND (status.run_after IS NULL OR status.run_after <= now())
                  AND run.status = 'processing'
                  AND COALESCE(progress.paused, 0) = 0
                  AND COALESCE(progress.cancelled, 0) = 0
                ORDER BY status.created_at, status.status_uuid
                FOR UPDATE OF status SKIP LOCKED
                LIMIT 1
            )
            UPDATE import_message_processing_status AS status
            SET status = 'running',
                lease_owner = ?,
                processing_attempt_uuid = ?,
                lease_acquired_at = now(),
                lease_expires_at = now() + (? * interval '1 second'),
                heartbeat_at = now(),
                attempt_started_at = now(),
                last_transition_at = now(),
                updated_at = now()
            FROM candidates
            WHERE status.status_uuid = candidates.status_uuid
            RETURNING status.*
            """,
            tuple(params),
        ).fetchall()
        self.connection.commit()
        return dict(rows[0]) if rows else None

    def release_claim(self, status_uuid: str, worker_id: str, attempt_uuid: str | None) -> bool:
        if not attempt_uuid:
            return False
        now = utc_now()
        with self.connection:
            return bool(
                self.connection.execute(
                    """
                    UPDATE import_message_processing_status
                    SET status = 'queued', lease_owner = NULL, lease_expires_at = NULL,
                        processing_attempt_uuid = NULL, updated_at = ?, last_transition_at = ?
                    WHERE status_uuid = ? AND status = 'running' AND lease_owner = ?
                      AND processing_attempt_uuid = ?
                    """,
                    (now, now, status_uuid, worker_id, attempt_uuid),
                ).rowcount
            )

    def renew_lease(
        self,
        status_uuid: str,
        worker_id: str,
        attempt_uuid: str | None,
        lease_seconds: int,
    ) -> bool:
        if not attempt_uuid:
            return False
        now = utc_now()
        with self.connection:
            return bool(
                self.connection.execute(
                    """
                    UPDATE import_message_processing_status AS owned
                    SET lease_expires_at = ?, heartbeat_at = ?, updated_at = ?
                    WHERE status_uuid = ? AND status = 'running' AND lease_owner = ?
                      AND processing_attempt_uuid = ?
                      AND lease_expires_at IS NOT NULL AND lease_expires_at >= ?
                      AND EXISTS (
                        SELECT 1 FROM import_runs run
                        WHERE run.import_run_uuid = owned.import_run_uuid
                          AND run.status = 'processing'
                      )
                      AND NOT EXISTS (
                        SELECT 1 FROM import_progress progress
                        WHERE progress.import_run_uuid = owned.import_run_uuid
                          AND COALESCE(progress.cancelled, 0) <> 0
                      )
                    """,
                    (
                        _add_seconds(now, lease_seconds),
                        now,
                        now,
                        status_uuid,
                        worker_id,
                        attempt_uuid,
                        now,
                    ),
                ).rowcount
            )

    def lease_fence_intact(
        self,
        *,
        status_uuid: str,
        worker_id: str,
        attempt_uuid: str | None,
        lock: bool = False,
        require_unexpired: bool = True,
    ) -> bool:
        if not attempt_uuid:
            return False
        now = utc_now()
        lock_clause = (
            " FOR UPDATE OF status"
            if lock
            and self.settings.runtime_profile == "full"
            and self.settings.canonical_store == "postgres"
            else ""
        )
        expiry_clause = "AND status.lease_expires_at >= ?" if require_unexpired else ""
        params: list[Any] = [status_uuid, worker_id, attempt_uuid]
        if require_unexpired:
            params.append(now)
        row = self.connection.execute(
            f"""
            SELECT status.status_uuid
            FROM import_message_processing_status status
            JOIN import_runs run ON run.import_run_uuid = status.import_run_uuid
            LEFT JOIN import_progress progress
              ON progress.import_run_uuid = status.import_run_uuid
            WHERE status.status_uuid = ?
              AND status.status = 'running'
              AND status.lease_owner = ?
              AND status.processing_attempt_uuid = ?
              AND status.lease_expires_at IS NOT NULL
              {expiry_clause}
              AND run.status = 'processing'
              AND COALESCE(progress.cancelled, 0) = 0
            {lock_clause}
            """,
            tuple(params),
        ).fetchone()
        return row is not None

    def recover_expired_leases(self) -> int:
        now = utc_now()
        with self.connection:
            return int(
                self.connection.execute(
                    """
                    UPDATE import_message_processing_status
                    SET status = 'queued', lease_owner = NULL, lease_expires_at = NULL,
                        processing_attempt_uuid = NULL, last_transition_at = ?, updated_at = ?
                    WHERE status = 'running'
                      AND lease_expires_at IS NOT NULL
                      AND lease_expires_at < ?
                    """,
                    (now, now, now),
                ).rowcount
            )

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
                        error_classification = NULL, run_after = NULL,
                        lease_owner = NULL, lease_expires_at = NULL,
                        processing_attempt_uuid = NULL, updated_at = ?, finished_at = NULL
                    WHERE status_uuid = ?
                    """,
                    (now, row["status_uuid"]),
                )
                if row["job_uuid"]:
                    self.connection.execute(
                        """
                        UPDATE jobs
                        SET status = 'pending', last_error = NULL,
                            last_error_sanitized = NULL, run_after = NULL,
                            locked_by = NULL, locked_at = NULL,
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
        provider_breakdown = self._provider_breakdown(import_run_uuid, statuses)
        last_error_row = self.connection.execute(
            """
            SELECT error_sanitized
            FROM import_message_processing_status
            WHERE import_run_uuid = ?
              AND error_sanitized IS NOT NULL
            ORDER BY updated_at DESC, status_uuid DESC
            LIMIT 1
            """,
            (import_run_uuid,),
        ).fetchone()
        last_error = str(last_error_row["error_sanitized"]) if last_error_row else None
        return {
            "import_run_uuid": import_run_uuid,
            "final_status": str(run["status"]) if run else "unknown",
            "last_error_sanitized": last_error,
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
            "terminal": all(str(item["status"]) in TERMINAL_STATUSES for item in statuses),
            "processing_jobs_retry_scheduled": self._retry_scheduled_count(import_run_uuid),
            "provider_breakdown": provider_breakdown,
            **metrics,
        }

    def _retry_scheduled_count(self, import_run_uuid: str) -> int:
        if self._is_postgres_runtime:
            row = self.connection.execute(
                """
                SELECT COUNT(*)
                FROM import_message_processing_status
                WHERE import_run_uuid = ? AND status = 'queued'
                  AND run_after IS NOT NULL AND run_after > now()
                """,
                (import_run_uuid,),
            ).fetchone()
        else:
            row = self.connection.execute(
                """
                SELECT COUNT(*)
                FROM import_message_processing_status
                WHERE import_run_uuid = ? AND status = 'queued'
                  AND run_after IS NOT NULL AND run_after > ?
                """,
                (import_run_uuid, utc_now()),
            ).fetchone()
        return int(row[0]) if row is not None else 0

    def _provider_breakdown(
        self, import_run_uuid: str, statuses: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        grouped: dict[tuple[str, str | None, str, str], dict[str, Any]] = {}
        for status in statuses:
            key = (
                str(status.get("model_role") or ModelRole.IMPORT_RECONSTRUCTION.value),
                str(status["model_profile_uuid"]) if status.get("model_profile_uuid") else None,
                str(status.get("provider_type") or "deterministic"),
                str(status.get("model_name") or "deterministic_extraction"),
            )
            item = grouped.setdefault(
                key,
                {
                    "operational_role": key[0],
                    "model_profile_uuid": key[1],
                    "provider_type": key[2],
                    "model_name": key[3],
                    "processing_jobs": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "succeeded": 0,
                    "failed": 0,
                    "_job_uuids": set(),
                },
            )
            item["processing_jobs"] += 1
            item["succeeded"] += int(status.get("status") in {"succeeded", "already_processed"})
            item["failed"] += int(status.get("status") == "failed")
            if status.get("job_uuid"):
                item["_job_uuids"].add(str(status["job_uuid"]))

        prompt_roles_by_job: dict[str, set[str]] = {}
        for row in self.connection.execute(
            """
            SELECT job_uuid, model_role
            FROM prompt_execution_runs
            WHERE import_run_uuid = ? AND job_uuid IS NOT NULL
            """,
            (import_run_uuid,),
        ).fetchall():
            prompt_roles_by_job.setdefault(str(row["job_uuid"]), set()).add(str(row["model_role"]))

        for row in self.connection.execute(
            """
            SELECT role, model_profile_uuid, provider_type, model_name,
                   COALESCE(SUM(input_tokens), 0) AS input_tokens,
                   COALESCE(SUM(output_tokens), 0) AS output_tokens
            FROM model_usage_events
            WHERE import_run_uuid = ?
            GROUP BY role, model_profile_uuid, provider_type, model_name
            """,
            (import_run_uuid,),
        ).fetchall():
            key = (
                str(row["role"]),
                str(row["model_profile_uuid"]) if row["model_profile_uuid"] else None,
                str(row["provider_type"]),
                str(row["model_name"]),
            )
            item = grouped.setdefault(
                key,
                {
                    "operational_role": key[0],
                    "model_profile_uuid": key[1],
                    "provider_type": key[2],
                    "model_name": key[3],
                    "processing_jobs": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "succeeded": 0,
                    "failed": 0,
                    "_job_uuids": set(),
                },
            )
            item["input_tokens"] += int(row["input_tokens"])
            item["output_tokens"] += int(row["output_tokens"])

        result: list[dict[str, Any]] = []
        for item in grouped.values():
            roles: set[str] = set()
            for job_uuid in item.pop("_job_uuids"):
                roles.update(prompt_roles_by_job.get(job_uuid, set()))
            if len(roles) == 1:
                prompt_role = next(iter(roles))
                if prompt_role != item["operational_role"]:
                    item["prompt_role"] = prompt_role
            result.append(item)
        return sorted(
            result,
            key=lambda item: (
                item["operational_role"],
                item["model_profile_uuid"] or "",
                item["provider_type"],
                item["model_name"],
            ),
        )

    def finalize_ready_runs(self, import_run_uuid: str | None = None) -> int:
        params: tuple[Any, ...] = ()
        run_filter = ""
        if import_run_uuid is not None:
            run_filter = "AND run.import_run_uuid = ?"
            params = (import_run_uuid,)
        placeholders = ", ".join("?" for _ in TERMINAL_STATUSES)
        rows = self.connection.execute(
            f"""
            SELECT run.import_run_uuid
            FROM import_runs run
            WHERE run.status = 'processing'
              {run_filter}
              AND NOT EXISTS (
                SELECT 1
                FROM import_message_processing_status status
                WHERE status.import_run_uuid = run.import_run_uuid
                  AND status.status NOT IN ({placeholders})
              )
            ORDER BY run.created_at, run.import_run_uuid
            """,
            params + tuple(sorted(TERMINAL_STATUSES)),
        ).fetchall()
        finalized = 0
        for row in rows:
            finalized += int(self.finalize_if_terminal(str(row["import_run_uuid"])))
        return finalized

    def finalize_if_terminal(self, import_run_uuid: str) -> bool:
        report = self.processing_report(import_run_uuid)
        progress = self.connection.execute(
            "SELECT paused, cancelled FROM import_progress WHERE import_run_uuid = ?",
            (import_run_uuid,),
        ).fetchone()
        if progress is not None and progress["cancelled"]:
            with self.connection:
                self.connection.execute(
                    "UPDATE import_runs SET status = 'cancelled' WHERE import_run_uuid = ?",
                    (import_run_uuid,),
                )
            return True
        if progress is not None and progress["paused"]:
            with self.connection:
                self.connection.execute(
                    "UPDATE import_runs SET status = 'paused' WHERE import_run_uuid = ?",
                    (import_run_uuid,),
                )
            return True
        if report["processing_jobs_total"] == 0:
            final_status = "fully_reconstructed"
        elif not report["terminal"]:
            return False
        elif report["processing_jobs_failed"] > 0:
            final_status = "completed_with_failures"
        else:
            final_status = "fully_reconstructed"
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

    def _process_one(self, status_row: dict[str, Any]) -> None:
        job_uuid = status_row.get("job_uuid")
        worker_id = str(status_row.get("lease_owner") or _worker_id())
        attempt_uuid = (
            str(status_row["processing_attempt_uuid"])
            if status_row.get("processing_attempt_uuid")
            else None
        )
        publication_fence_locked = False

        def require_fence(*, lock: bool = False) -> None:
            nonlocal publication_fence_locked
            if not self.lease_fence_intact(
                status_uuid=str(status_row["status_uuid"]),
                worker_id=worker_id,
                attempt_uuid=attempt_uuid,
                lock=lock,
                require_unexpired=not publication_fence_locked,
            ):
                raise ImportLeaseLost("lease_lost")
            if lock:
                publication_fence_locked = True

        try:
            require_fence()
            now = utc_now()
            if job_uuid:
                with self.connection:
                    updated = self.connection.execute(
                        """
                        UPDATE jobs
                        SET status = 'running', attempts = attempts + 1, locked_by = ?,
                            locked_at = ?, updated_at = ?
                        WHERE job_uuid = ?
                          AND EXISTS (
                            SELECT 1 FROM import_message_processing_status status
                            WHERE status.status_uuid = ?
                              AND status.status = 'running'
                              AND status.lease_owner = ?
                              AND status.processing_attempt_uuid = ?
                              AND status.lease_expires_at IS NOT NULL
                              AND status.lease_expires_at >= ?
                              AND EXISTS (
                                SELECT 1 FROM import_runs run
                                WHERE run.import_run_uuid = status.import_run_uuid
                                  AND run.status = 'processing'
                              )
                              AND NOT EXISTS (
                                SELECT 1 FROM import_progress progress
                                WHERE progress.import_run_uuid = status.import_run_uuid
                                  AND COALESCE(progress.cancelled, 0) <> 0
                              )
                          )
                        """,
                        (
                            worker_id,
                            now,
                            now,
                            job_uuid,
                            status_row["status_uuid"],
                            worker_id,
                            attempt_uuid,
                            now,
                        ),
                    ).rowcount
                    if not updated:
                        raise ImportLeaseLost("lease_lost")

            pipeline = (
                PostgresMemoryWorkerPipeline(self.connection, self.settings)
                if self._is_postgres_runtime
                else MemoryWorkerPipeline(self.connection, self.settings)
            )
            prepared_target = self._hydrate_execution_model_target(status_row)
            prepared = pipeline.prepare_message(
                str(status_row["target_message_uuid"]),
                model_target=prepared_target,
            )

            with self._result_transaction():
                require_fence(lock=True)
                commit_target = self._hydrate_execution_model_target(status_row)
                self._require_same_execution_target(
                    status_row=status_row,
                    prepared_target=prepared_target,
                    commit_target=commit_target,
                    prepared=prepared,
                )
                result = pipeline.process_message(
                    str(status_row["target_message_uuid"]),
                    import_run_uuid=str(status_row["import_run_uuid"]),
                    job_uuid=str(job_uuid) if job_uuid else None,
                    model_target=commit_target,
                    lease_fence=require_fence,
                    prepared_inference=prepared,
                )
                processing_run_uuid = str(result["processing_run_uuid"])
                prompt_execution_uuid = result.get("prompt_execution_uuid")
                if prompt_execution_uuid is None:
                    execution = self.connection.execute(
                        """
                        SELECT prompt_execution_uuid
                        FROM prompt_execution_runs
                        WHERE import_run_uuid = ? AND message_uuid = ?
                          AND job_uuid = ?
                        ORDER BY created_at DESC
                        LIMIT 1
                        """,
                        (
                            status_row["import_run_uuid"],
                            status_row["target_message_uuid"],
                            job_uuid,
                        ),
                    ).fetchone()
                    prompt_execution_uuid = (
                        execution["prompt_execution_uuid"] if execution else None
                    )
                finished = utc_now()
                success_updated = self.connection.execute(
                    """
                    UPDATE import_message_processing_status AS owned
                    SET status = 'succeeded', processing_stage = 'complete',
                        memory_processing_run_uuid = ?, prompt_execution_uuid = ?,
                        error_sanitized = NULL, error_classification = NULL, run_after = NULL,
                        updated_at = ?, finished_at = ?, lease_owner = NULL,
                        lease_expires_at = NULL, last_transition_at = ?
                    WHERE status_uuid = ? AND status = 'running' AND lease_owner = ?
                      AND processing_attempt_uuid = ?
                      AND lease_expires_at IS NOT NULL
                      AND EXISTS (
                        SELECT 1 FROM import_runs run
                        WHERE run.import_run_uuid = owned.import_run_uuid
                          AND run.status = 'processing'
                      )
                      AND NOT EXISTS (
                        SELECT 1 FROM import_progress progress
                        WHERE progress.import_run_uuid = owned.import_run_uuid
                          AND COALESCE(progress.cancelled, 0) <> 0
                      )
                    """,
                    (
                        processing_run_uuid,
                        prompt_execution_uuid,
                        finished,
                        finished,
                        finished,
                        status_row["status_uuid"],
                        worker_id,
                        attempt_uuid,
                    ),
                ).rowcount
                if not success_updated:
                    raise ImportLeaseLost("lease_lost")
                if job_uuid:
                    self.connection.execute(
                        """
                        UPDATE jobs
                        SET status = 'succeeded', locked_by = NULL, locked_at = NULL,
                            last_error = NULL, last_error_sanitized = NULL, run_after = NULL,
                            updated_at = ?
                        WHERE job_uuid = ? AND locked_by = ?
                        """,
                        (finished, job_uuid, worker_id),
                    )
        except ImportLeaseLost:
            self.connection.rollback()
            return
        except Exception as error:
            self.connection.rollback()
            publication_fence_locked = False
            sanitized = (
                sanitize_error_message(f"{type(error).__name__}: {error}") or type(error).__name__
            )
            finished = utc_now()
            transition = _failure_transition(status_row, type(error).__name__, sanitized, finished)
            try:
                with self._result_transaction():
                    require_fence(lock=True)
                    failure_updated = self.connection.execute(
                        """
                        UPDATE import_message_processing_status AS owned
                        SET status = ?, error_sanitized = ?, error_classification = ?,
                            retry_count = retry_count + ?, run_after = ?, updated_at = ?,
                            finished_at = ?, lease_owner = NULL, lease_expires_at = NULL,
                            last_transition_at = ?
                        WHERE status_uuid = ? AND status = 'running' AND lease_owner = ?
                          AND processing_attempt_uuid = ?
                          AND lease_expires_at IS NOT NULL
                          AND EXISTS (
                            SELECT 1 FROM import_runs run
                            WHERE run.import_run_uuid = owned.import_run_uuid
                              AND run.status = 'processing'
                          )
                          AND NOT EXISTS (
                            SELECT 1 FROM import_progress progress
                            WHERE progress.import_run_uuid = owned.import_run_uuid
                              AND COALESCE(progress.cancelled, 0) <> 0
                          )
                        """,
                        transition
                        + (
                            status_row["status_uuid"],
                            worker_id,
                            attempt_uuid,
                        ),
                    ).rowcount
                    if not failure_updated:
                        raise ImportLeaseLost("lease_lost")
                    if job_uuid:
                        self.connection.execute(
                            """
                            UPDATE jobs
                            SET status = ?, last_error = ?, last_error_sanitized = ?,
                                run_after = ?, locked_by = NULL, locked_at = NULL, updated_at = ?
                            WHERE job_uuid = ? AND locked_by = ?
                            """,
                            (
                                "pending" if transition[0] == "queued" else "failed",
                                sanitized,
                                sanitized,
                                transition[4],
                                finished,
                                job_uuid,
                                worker_id,
                            ),
                        )
                    self.model_control.record_usage_event(
                        UsageEventCreate(
                            role=ModelRole(
                                str(
                                    status_row.get("model_role")
                                    or ModelRole.IMPORT_RECONSTRUCTION.value
                                )
                            ),
                            stage="import_memory_reconstruction",
                            model_profile_uuid=status_row.get("model_profile_uuid"),
                            provider_type=str(status_row.get("provider_type") or "deterministic"),
                            model_name=str(
                                status_row.get("model_name") or "deterministic_extraction"
                            ),
                            session_uuid=status_row.get("target_session_uuid"),
                            message_uuid=status_row.get("target_message_uuid"),
                            import_run_uuid=status_row.get("import_run_uuid"),
                            job_uuid=job_uuid,
                            status="error",
                            error_class=type(error).__name__,
                            error_message_sanitized=sanitized,
                        )
                    )
            except ImportLeaseLost:
                self.connection.rollback()
                return

    @property
    def _is_postgres_runtime(self) -> bool:
        return (
            self.settings.runtime_profile == "full" and self.settings.canonical_store == "postgres"
        )

    @contextmanager
    def _result_transaction(self) -> Iterator[None]:
        if self._is_postgres_runtime:
            with self.connection.raw.transaction():
                yield
            return
        if not isinstance(self.connection, NestedTransactionConnection):
            raise RuntimeError("SQLite import result requires atomic connection support")
        with self.connection.atomic(immediate=True):
            yield

    @staticmethod
    def _require_same_execution_target(
        *,
        status_row: dict[str, Any],
        prepared_target: dict[str, Any],
        commit_target: dict[str, Any],
        prepared: Any,
    ) -> None:
        fields = ("model_role", "model_profile_uuid", "provider_type", "model_name")
        scheduled = {field: status_row.get(field) for field in fields}
        if scheduled["model_role"] is None:
            scheduled["model_role"] = ModelRole.IMPORT_RECONSTRUCTION.value
        if scheduled["provider_type"] is None:
            scheduled["provider_type"] = "deterministic"
        if scheduled["model_name"] is None:
            scheduled["model_name"] = "deterministic_extraction"
        prepared_identity = {field: getattr(prepared, field) for field in fields}
        prepared_target_identity = {field: prepared_target.get(field) for field in fields}
        commit_target_identity = {field: commit_target.get(field) for field in fields}
        if not (
            scheduled == prepared_identity == prepared_target_identity == commit_target_identity
        ):
            raise RuntimeError("scheduled model profile execution identity changed")
        if (
            str(status_row.get("processing_identity_hash") or "")
            != prepared.processing_identity_hash
            or str(status_row.get("input_content_hash") or "") != prepared.input_content_hash
        ):
            raise RuntimeError("scheduled message processing identity changed")

    def _hydrate_execution_model_target(self, status_row: dict[str, Any]) -> dict[str, Any]:
        """Resolve the complete provider profile at execution without persisting secrets."""
        operational_role = str(
            status_row.get("model_role") or ModelRole.IMPORT_RECONSTRUCTION.value
        )
        profile_uuid = status_row.get("model_profile_uuid")
        if not profile_uuid:
            if not self.settings.import_reconstruction_allow_deterministic_fallback:
                raise RuntimeError(
                    "scheduled deterministic fallback is no longer allowed by policy"
                )
            return {
                "model_role": operational_role,
                "model_profile_uuid": None,
                "provider_type": "deterministic",
                "model_name": "deterministic_extraction",
            }
        profile = self.model_control.get_profile(str(profile_uuid))
        if profile is None:
            raise RuntimeError("scheduled model profile is no longer available")
        if str(profile.model_profile_uuid) != str(profile_uuid):
            raise RuntimeError("scheduled model profile identity mismatch")
        if not profile.is_enabled:
            raise RuntimeError("scheduled model profile is disabled")
        if profile.role.value != operational_role:
            raise RuntimeError("scheduled model profile role is not compatible")
        provider_type = str(profile.provider_type or profile.provider or "")
        if provider_type not in {"deterministic", "openai_compatible", "openai_compatible_llm"}:
            raise RuntimeError("scheduled model profile provider type is unsupported")
        if not str(profile.model_name or "").strip():
            raise RuntimeError("scheduled model profile model name is required")
        if provider_type in {"openai_compatible", "openai_compatible_llm"}:
            if not profile.endpoint_url:
                raise RuntimeError("scheduled model profile endpoint is required")
            if not (profile.supports_structured_output or profile.supports_json_mode):
                raise RuntimeError(
                    "scheduled model profile structured output capability is required"
                )
            if profile.secret_strategy in {"env_var", "environment_reference"}:
                if not profile.secret_env_var_name:
                    raise RuntimeError(
                        "scheduled model profile secret environment variable is required"
                    )
                if not os.getenv(profile.secret_env_var_name):
                    raise RuntimeError(
                        "scheduled model profile secret environment variable is not set"
                    )
            if profile.requires_privacy_acknowledgement and not profile.privacy_acknowledged_at:
                raise RuntimeError("scheduled model profile requires privacy acknowledgement")
        scheduled_provider = str(status_row.get("provider_type") or provider_type)
        scheduled_model = str(status_row.get("model_name") or profile.model_name or "")
        if provider_type != scheduled_provider:
            raise RuntimeError("scheduled model profile provider type changed")
        if str(profile.model_name) != scheduled_model:
            raise RuntimeError("scheduled model profile model name changed")
        hydrated = cast(dict[str, Any], profile.model_dump(mode="json"))
        hydrated["model_role"] = operational_role
        hydrated["provider_type"] = provider_type
        hydrated["model_name"] = str(profile.model_name)
        return hydrated

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
            "model_role": values.get("model_role"),
            "model_profile_uuid": values.get("model_profile_uuid"),
            "provider_type": values.get("provider_type"),
            "model_name": values.get("model_name"),
            "pipeline_version": values.get("pipeline_version"),
            "prompt_bundle_version": values.get("prompt_bundle_version"),
            "prompt_id": values.get("prompt_id"),
            "prompt_version": values.get("prompt_version"),
            "input_content_hash": values.get("input_content_hash"),
            "retry_count": 0,
            "error_sanitized": values.get("error_sanitized"),
            "processing_identity_hash": values.get("processing_identity_hash"),
            "created_at": now,
            "updated_at": now,
            "last_transition_at": now,
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
        outbox_reference = (
            "source_uuid"
            if self.settings.runtime_profile == "full"
            and self.settings.canonical_store == "postgres"
            else "aggregate_uuid"
        )
        outbox = self.connection.execute(
            f"""
            SELECT COUNT(DISTINCT g.outbox_uuid)
            FROM graph_projection_outbox g
            WHERE g.{outbox_reference} IN (
                SELECT mv.memory_uuid
                FROM memory_versions mv
                JOIN memory_candidates mc ON mc.candidate_uuid = mv.source_candidate_uuid
                JOIN text_units tu ON tu.text_unit_uuid = mc.text_unit_uuid
                WHERE tu.message_uuid IN ({placeholders})
            ) OR g.{outbox_reference} IN (
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


def _worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{threading.get_ident()}:{uuid.uuid4()}"


def _add_seconds(timestamp: str, seconds: int) -> str:
    return (datetime.fromisoformat(timestamp) + timedelta(seconds=seconds)).isoformat()


def _classify_error(error_class: str, message: str) -> tuple[str, bool]:
    lowered = message.lower()
    if error_class in {"TimeoutError", "ConnectionError"} or "timeout" in lowered:
        return "retryable_network", True
    if "429" in lowered:
        return "retryable_provider_429", True
    if any(f" {code}" in lowered or str(code) in lowered for code in range(500, 600)):
        return "retryable_provider_5xx", True
    if (
        "privacy acknowledgement" in lowered
        or "secret" in lowered
        or "scheduled model profile" in lowered
        or "deterministic fallback" in lowered
    ):
        return "configuration_error", False
    if "400" in lowered or "invalid request" in lowered:
        return "invalid_request", False
    return "worker_error", False


def _failure_transition(
    status_row: dict[str, Any], error_class: str, sanitized: str, finished: str
) -> tuple[Any, ...]:
    classification, retryable = _classify_error(error_class, sanitized)
    retry_count = int(status_row.get("retry_count") or 0)
    max_attempts = int(status_row.get("max_attempts") or 5)
    if retryable and retry_count + 1 < max_attempts:
        delay = min(900, 10 * (2**retry_count))
        return (
            "queued",
            sanitized,
            classification,
            1,
            _add_seconds(finished, delay),
            finished,
            None,
            finished,
        )
    return ("failed", sanitized, classification, 0, None, finished, finished, finished)


def _model_control_repository(connection: Any, settings: Settings) -> Any:
    if settings.runtime_profile == "full" and settings.canonical_store == "postgres":
        return PostgresModelControlRepository(connection)
    return ModelControlRepository(connection)
