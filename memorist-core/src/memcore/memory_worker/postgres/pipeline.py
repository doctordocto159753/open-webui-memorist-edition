from __future__ import annotations

# ruff: noqa: E501
import json
from collections.abc import Callable
from hashlib import sha256
from time import perf_counter
from types import SimpleNamespace
from typing import Any, cast

from fastapi import HTTPException

from memcore.config import Settings
from memcore.memory_worker.analysis.modality import modality_payload
from memcore.memory_worker.attempt_audit import (
    FrozenProviderExecution,
    ProviderAttemptAuditRepository,
    stable_stage_execution_uuid,
)
from memcore.memory_worker.contracts import PIPELINE_VERSION, PROMPT_BUNDLE_VERSION
from memcore.memory_worker.fencing import LeaseFenceRejected, fenced_write
from memcore.memory_worker.gating import DeterministicGate
from memcore.memory_worker.identity import (
    build_processing_identity,
    execution_profile_fingerprint,
)
from memcore.memory_worker.jakobson_runtime import execute_jakobson_contract
from memcore.memory_worker.message_semantics import update_semantic_job_outcome
from memcore.memory_worker.postgres.deterministic_fallback import (
    deterministic_jakobson_output,
)
from memcore.memory_worker.postgres.gated_candidate_adapter import (
    record_candidates,
)
from memcore.memory_worker.postgres.routing_policy_adapter import record_routes
from memcore.memory_worker.prepared import PreparedJakobsonInference
from memcore.memory_worker.prompts import render_prompt, validate_prompt_execution
from memcore.memory_worker.prompts.contracts import canonical_sentence_items, get_contract
from memcore.memory_worker.prompts.versions import (
    JAKOBSON_SENTENCE_ANALYSIS_ACTIVE_VERSION,
    JAKOBSON_SENTENCE_ANALYSIS_PROMPT_ID,
    PROMPT_PACK_VERSION,
)
from memcore.memory_worker.providers.openai_compatible import (
    OpenAICompatibleMemoryExtractionProvider,
)
from memcore.memory_worker.segmentation.sentence_segmenter import SentenceSegmenter
from memcore.memory_worker.semantic.orchestration import (
    SemanticCandidatePlanningRequest,
    SemanticCandidatePlanningService,
)
from memcore.memory_worker.semantic.runtime_adapters import (
    PostgresSemanticCandidateRuntimeAdapter,
)
from memcore.model_control.postgres_repository import PostgresModelControlRepository
from memcore.model_control.resolution import RoleResolutionService
from memcore.model_control.security import sanitize_error_message
from memcore.model_control.stage_contracts import (
    deterministic_high_confidence,
    deterministic_privacy,
    validate_high_confidence_result,
    validate_privacy_result,
)
from memcore.model_control.stage_invocation import StageInvocationRequest, StageInvoker
from memcore.model_control.stage_status import stage_status_for_output
from memcore.models import ModelRole, new_uuid, utc_now
from memcore.textsemantics import coerce_polarity
from memcore.validators.ijson import canonical_hash_ijson


class PostgresMemoryWorkerPipeline:
    def __init__(self, connection: Any, settings: Settings) -> None:
        self.connection = connection
        self.settings = settings
        self.segmenter = SentenceSegmenter()
        self.gate = DeterministicGate()
        self.semantic_candidate_planning = SemanticCandidatePlanningService(
            PostgresSemanticCandidateRuntimeAdapter(connection)
        )
        self.provider_job_uuid: str | None = None
        self.provider_lease_fence: Callable[[], None] | None = None

    def execution_snapshot(
        self,
        message_uuid: str,
        model_target: dict[str, Any] | None = None,
    ) -> dict[str, str | None]:
        message = self.connection.execute(
            "SELECT m.*, s.workspace_uuid, s.project_uuid FROM messages m "
            "JOIN sessions s ON s.session_uuid = m.session_uuid "
            "WHERE m.message_uuid = %s",
            (message_uuid,),
        ).fetchone()
        if message is None:
            raise HTTPException(status_code=404, detail="message not found")
        profile = model_target or self._resolve_profile(message) or {}
        model_role = str(
            profile.get("model_role") or profile.get("role") or "import_reconstruction"
        )
        identity = build_processing_identity(
            target_message_uuid=message_uuid,
            raw_text=str(message.get("raw_text") or ""),
            model_target=profile,
            model_role=model_role,
        )
        return {
            "input_content_hash": identity.input_content_hash,
            "processing_identity_hash": identity.identity_hash,
            "profile_fingerprint": execution_profile_fingerprint(profile),
            "contract_hash": _active_contract_hash(),
        }

    def prepare_message(
        self,
        message_uuid: str,
        model_target: dict[str, Any] | None = None,
        *,
        job_uuid: str | None = None,
        lease_fence: Callable[[], None] | None = None,
    ) -> PreparedJakobsonInference:
        """Run and validate provider inference before opening a write transaction."""
        job_uuid = job_uuid or self.provider_job_uuid
        lease_fence = lease_fence or self.provider_lease_fence
        explicit_override = model_target is not None
        message = self.connection.execute(
            "SELECT m.*, s.workspace_uuid, s.project_uuid FROM messages m JOIN sessions s ON s.session_uuid = m.session_uuid WHERE m.message_uuid = %s",
            (message_uuid,),
        ).fetchone()
        if message is None:
            raise HTTPException(status_code=404, detail="message not found")
        raw_text = str(message.get("raw_text") or "").strip()
        if not raw_text:
            raise HTTPException(status_code=400, detail="message has no raw_text to process")
        profile = model_target or self._resolve_profile(message) or {}
        model_role = str(
            profile.get("model_role") or profile.get("role") or "import_reconstruction"
        )
        provider_type = str(
            profile.get("provider_type") or profile.get("provider") or "deterministic"
        )
        model_profile_uuid = (
            str(profile["model_profile_uuid"]) if profile.get("model_profile_uuid") else None
        )
        model_name = str(profile.get("model_name") or provider_type)
        identity = build_processing_identity(
            target_message_uuid=message_uuid,
            raw_text=raw_text,
            model_target={
                "model_profile_uuid": model_profile_uuid,
                "provider_type": provider_type,
                "model_name": model_name,
            },
            model_role=model_role,
        )
        expected_snapshot = {
            "input_content_hash": identity.input_content_hash,
            "processing_identity_hash": identity.identity_hash,
            "profile_fingerprint": execution_profile_fingerprint(profile),
            "contract_hash": _active_contract_hash(),
        }
        units = self.segmenter.to_text_units(
            message_uuid=message_uuid,
            session_uuid=str(message["session_uuid"]),
            speaker_role=str(message["role"]),
            text=raw_text,
        )
        input_payload = {
            "sentences": [
                {
                    "id": index + 1,
                    "unit_uuid": unit.text_unit_uuid,
                    "message_uuid": message_uuid,
                    "text": unit.text,
                    "span_start": unit.start_char,
                    "span_end": unit.end_char,
                }
                for index, unit in enumerate(units)
            ]
        }
        is_remote = provider_type in {"openai_compatible", "openai_compatible_llm"}
        processing_run_uuid: str | None = None
        stage_execution_uuid: str | None = None
        attempt_audit: ProviderAttemptAuditRepository | None = None
        if is_remote:
            with fenced_write(self.connection, lease_fence, postgres=True):
                run = self._get_or_create_run(
                    message,
                    identity,
                    model_profile_uuid,
                    provider_type,
                    model_name,
                )
                processing_run_uuid = str(run["processing_run_uuid"])
            authority_key = (
                f"{processing_run_uuid}:{message_uuid}:{identity.identity_hash}:"
                f"{_active_contract_hash()}"
            )
            stage_execution_uuid = stable_stage_execution_uuid(authority_key)
            frozen = FrozenProviderExecution(
                stage_execution_uuid=stage_execution_uuid,
                processing_run_uuid=processing_run_uuid,
                job_uuid=job_uuid,
                source_type="message",
                source_uuid=message_uuid,
                requested_role=str(profile.get("requested_role") or model_role),
                effective_role=str(profile.get("effective_role") or model_role),
                model_profile_uuid=model_profile_uuid,
                profile_fingerprint=expected_snapshot["profile_fingerprint"],
                scope_source=str(profile.get("scope_source") or "explicit_override"),
                inheritance_source=(
                    str(profile["inheritance_source"])
                    if profile.get("inheritance_source")
                    else None
                ),
                provider_type=provider_type,
                model_name=model_name,
                capability_mode=_capability_mode(profile),
                prompt_id=identity.prompt_id,
                prompt_version=identity.prompt_version,
                contract_hash=_active_contract_hash(),
                input_hash=identity.input_content_hash,
                idempotency_identity=authority_key,
                deterministic_fallback_version="jakobson-deterministic-v1",
            )
            attempt_audit = ProviderAttemptAuditRepository(self.connection, frozen, postgres=True)
        else:
            self.connection.commit()
        speaker_role = str(message["role"])

        def _deterministic() -> dict[str, Any]:
            return self._deterministic_jakobson_output(
                [
                    {
                        "text_unit_uuid": unit.text_unit_uuid,
                        "text": unit.text,
                        "speaker_role": speaker_role,
                        "start_char": unit.start_char,
                        "end_char": unit.end_char,
                    }
                    for unit in units
                ]
            )

        execution_profile = {
            **profile,
            "provider_type": provider_type,
            "model_name": model_name,
            "model_profile_uuid": model_profile_uuid,
        }

        def _revalidate() -> None:
            if lease_fence is not None:
                cast(Any, lease_fence)(before_provider=True)
            snapshot_target = profile if explicit_override else None
            if self.execution_snapshot(message_uuid, snapshot_target) != expected_snapshot:
                raise LeaseFenceRejected("provider execution authority changed")

        outcome = execute_jakobson_contract(
            profile=execution_profile,
            input_payload=input_payload,
            deterministic_builder=_deterministic,
            revalidate=_revalidate if is_remote else None,
            attempt_audit=attempt_audit,
        )
        output = outcome.output
        return PreparedJakobsonInference(
            message_uuid=message_uuid,
            model_role=model_role,
            model_profile_uuid=model_profile_uuid,
            provider_type=provider_type,
            model_name=model_name,
            processing_identity_hash=identity.identity_hash,
            input_content_hash=identity.input_content_hash,
            profile_fingerprint=execution_profile_fingerprint(profile),
            requested_role=str(profile.get("requested_role") or model_role),
            effective_role=str(profile.get("effective_role") or model_role),
            scope_source=str(profile.get("scope_source") or "explicit_override"),
            inheritance_source=(
                str(profile["inheritance_source"]) if profile.get("inheritance_source") else None
            ),
            contract_hash=_active_contract_hash(),
            output=output,
            input_tokens=(
                outcome.input_tokens
                if outcome.called_provider
                else max(0, (len(raw_text) + 3) // 4)
            ),
            output_tokens=outcome.output_tokens or len(canonical_sentence_items(output)),
            latency_ms=outcome.latency_ms,
            prompt_version=JAKOBSON_SENTENCE_ANALYSIS_ACTIVE_VERSION,
            called_provider=outcome.called_provider,
            provider_output_valid=outcome.provider_output_valid,
            canonicalized=outcome.canonicalized,
            repair_attempted=outcome.repair_attempted,
            repair_succeeded=outcome.repair_succeeded,
            fallback_used=outcome.fallback_used,
            fallback_reason=outcome.fallback_reason,
            capability_mode=outcome.capability_mode,
            provider_response_id=outcome.provider_response_id,
            parse_status=outcome.parse_status,
            validation_error_paths=outcome.validation_error_paths,
            processing_run_uuid=processing_run_uuid,
            stage_execution_uuid=stage_execution_uuid,
            job_uuid=job_uuid,
            attempt_count=outcome.attempt_count,
        )

    def process_message(
        self,
        message_uuid: str,
        import_run_uuid: str | None = None,
        job_uuid: str | None = None,
        model_target: dict[str, Any] | None = None,
        lease_fence: Callable[[], None] | None = None,
        prepared_inference: PreparedJakobsonInference | None = None,
    ) -> dict[str, object]:
        started = perf_counter()
        message = self.connection.execute(
            "SELECT m.*, s.workspace_uuid, s.project_uuid FROM messages m JOIN sessions s ON s.session_uuid = m.session_uuid WHERE m.message_uuid = %s",
            (message_uuid,),
        ).fetchone()
        if message is None:
            raise HTTPException(status_code=404, detail="message not found")
        raw_text = str(message.get("raw_text") or "").strip()
        if not raw_text:
            raise HTTPException(status_code=400, detail="message has no raw_text to process")
        profile = model_target or self._resolve_profile(message)
        model_role = str(
            (profile or {}).get("model_role")
            or (profile or {}).get("role")
            or "import_reconstruction"
        )
        provider_type = str(
            (profile or {}).get("provider_type")
            or (profile or {}).get("provider")
            or "deterministic"
        )
        model_profile_uuid = (
            str(profile["model_profile_uuid"])
            if profile and profile.get("model_profile_uuid")
            else None
        )
        model_name = str((profile or {}).get("model_name") or provider_type)
        if prepared_inference is None:
            prepared_inference = self.prepare_message(
                message_uuid,
                model_target,
                job_uuid=job_uuid,
                lease_fence=lease_fence,
            )
        if provider_type == "disabled":
            provider_type = "deterministic"
            model_profile_uuid = None
            model_name = "deterministic_extraction"
        identity = build_processing_identity(
            target_message_uuid=message_uuid,
            raw_text=raw_text,
            model_target={
                "model_profile_uuid": model_profile_uuid,
                "provider_type": provider_type,
                "model_name": model_name,
            },
            model_role=model_role,
        )
        if prepared_inference is not None and (
            prepared_inference.processing_identity_hash != identity.identity_hash
            or prepared_inference.input_content_hash != identity.input_content_hash
            or prepared_inference.profile_fingerprint != execution_profile_fingerprint(profile)
        ):
            raise RuntimeError("prepared inference processing identity mismatch")
        content_hash = identity.input_content_hash
        try:
            with fenced_write(self.connection, lease_fence, postgres=True):
                run = self._get_or_create_run(
                    message, identity, model_profile_uuid, provider_type, model_name
                )
                if run["status"] == "succeeded":
                    completed = self._summary(
                        message_uuid,
                        str(run["processing_run_uuid"]),
                        True,
                    )
                else:
                    completed = None
                    self.connection.execute(
                        "UPDATE memory_processing_runs SET status = 'running', "
                        "started_at = COALESCE(started_at, %s) "
                        "WHERE processing_run_uuid = %s",
                        (utc_now(), run["processing_run_uuid"]),
                    )
                if completed is not None:
                    return completed
                units = self._unitize(message, raw_text)
                input_payload = {
                    "sentences": [
                        {
                            "id": index + 1,
                            "unit_uuid": unit["text_unit_uuid"],
                            "message_uuid": message_uuid,
                            "text": unit["text"],
                            "span_start": unit["start_char"],
                            "span_end": unit["end_char"],
                        }
                        for index, unit in enumerate(units)
                    ]
                }
                self._validate_prepared_inference(
                    prepared_inference,
                    message_uuid=message_uuid,
                    model_role=model_role,
                    model_profile_uuid=model_profile_uuid,
                    provider_type=provider_type,
                    model_name=model_name,
                )
                output = prepared_inference.output
                prompt_version = prepared_inference.prompt_version
                validate_prompt_execution(
                    JAKOBSON_SENTENCE_ANALYSIS_PROMPT_ID,
                    prompt_version,
                    input_payload,
                    output,
                )
                if lease_fence is not None:
                    lease_fence()
                prompt_execution_uuid = self._record_prompt_execution(
                    input_payload=input_payload,
                    output=output,
                    message=message,
                    model_profile_uuid=model_profile_uuid,
                    provider_type=provider_type,
                    model_name=model_name,
                    model_role=model_role,
                    latency_ms=prepared_inference.latency_ms,
                    input_tokens=prepared_inference.input_tokens,
                    output_tokens=prepared_inference.output_tokens,
                    import_run_uuid=import_run_uuid,
                    job_uuid=job_uuid,
                    prompt_version=prompt_version,
                )
                self._record_extraction_stage(
                    prepared_inference,
                    processing_run_uuid=str(run["processing_run_uuid"]),
                    message=message,
                    input_payload=input_payload,
                    output=output,
                    model_profile_uuid=model_profile_uuid,
                    provider_type=provider_type,
                    model_name=model_name,
                    model_role=model_role,
                    prompt_version=prompt_version,
                )
                usage = {
                    "input_tokens": prepared_inference.input_tokens,
                    "output_tokens": prepared_inference.output_tokens,
                    "latency_ms": prepared_inference.latency_ms,
                }
                self._record_usage(
                    message,
                    model_profile_uuid,
                    provider_type,
                    model_name,
                    model_role,
                    import_run_uuid,
                    job_uuid,
                    usage,
                    status=stage_status_for_output(
                        output.get("status"),
                        fallback_used=prepared_inference.fallback_used,
                        provider_failed=not prepared_inference.provider_output_valid,
                    ).value,
                )
                analysis_run_uuid = self._record_jakobson(
                    message,
                    input_payload,
                    output,
                    model_profile_uuid,
                    provider_type,
                    model_name,
                    prompt_execution_uuid,
                    prompt_version,
                )
                annotations = self._record_annotations(
                    message_uuid,
                    analysis_run_uuid,
                    units,
                    output,
                )
                routes = self._record_routes(message_uuid, annotations)
                decisions = self._record_gates(str(run["processing_run_uuid"]), units)
                analyses = self._record_linguistic_analyses(
                    str(run["processing_run_uuid"]), units, output
                )
            semantic_result = self.semantic_candidate_planning.execute(
                SemanticCandidatePlanningRequest(
                    message_uuid=message_uuid,
                    processing_run_uuid=str(run["processing_run_uuid"]),
                    profile=dict(profile or {}),
                    import_run_uuid=import_run_uuid,
                    job_uuid=job_uuid,
                    lease_fence=lease_fence,
                )
            )
            candidates = self._semantic_candidate_rows(list(semantic_result.candidate_uuids))
            candidate_stage_results = self._run_candidate_stages(
                str(run["processing_run_uuid"]),
                message,
                candidates,
                lease_fence=lease_fence,
            )
            with fenced_write(self.connection, lease_fence, postgres=True):
                memories = self._record_memories(
                    message,
                    candidates,
                    content_hash,
                    semantic_result.semantic_prompt_execution_uuid or prompt_execution_uuid,
                )
            embedding_results = self._run_embedding_stages(
                str(run["processing_run_uuid"]),
                message,
                candidates,
                lease_fence=lease_fence,
            )
            with fenced_write(self.connection, lease_fence, postgres=True):
                semantic_outcome = update_semantic_job_outcome(
                    self.connection,
                    postgres=True,
                    processing_run_uuid=str(run["processing_run_uuid"]),
                    candidate_count=len(candidates),
                    memory_count=int(memories),
                    partial=bool(semantic_result.plan.warnings),
                )
                self.connection.execute(
                    "UPDATE messages SET processing_status = 'available', updated_at = %s "
                    "WHERE message_uuid = %s",
                    (utc_now(), message_uuid),
                )
                self.connection.execute(
                    "UPDATE memory_processing_runs SET status = 'succeeded', finished_at = %s "
                    "WHERE processing_run_uuid = %s",
                    (utc_now(), run["processing_run_uuid"]),
                )
            return {
                **self._summary(message_uuid, str(run["processing_run_uuid"]), False),
                "prompt_execution_uuid": prompt_execution_uuid,
                "model_profile_uuid": model_profile_uuid,
                "jakobson_annotations": len(annotations),
                "memory_signal_routes": len(routes),
                "gate_decisions": len(decisions),
                "analyses": len(analyses),
                "semantic_coverage_hash": semantic_result.plan.coverage_hash,
                "semantic_coverage_status": semantic_result.plan.status,
                "semantic_proposals": semantic_result.proposal_count,
                "semantic_outcome": semantic_outcome,
                "semantic_prompt_execution_uuid": (semantic_result.semantic_prompt_execution_uuid),
                "semantic_stage_execution_uuid": (semantic_result.semantic_stage_execution_uuid),
                "semantic_context_items": semantic_result.context_item_count,
                "semantic_terminal_gate_short_circuit": (
                    semantic_result.terminal_gate_short_circuit
                ),
                "latency_ms": int((perf_counter() - started) * 1000),
                "memories_created": memories,
                "candidate_stage_results": candidate_stage_results,
                "embedding_results": embedding_results,
            }
        except LeaseFenceRejected:
            self.connection.rollback()
            raise
        except Exception as error:
            if "run" in locals():
                with fenced_write(self.connection, lease_fence, postgres=True):
                    self.connection.execute(
                        "UPDATE memory_processing_runs SET status = 'failed', "
                        "finished_at = %s, error_sanitized = %s "
                        "WHERE processing_run_uuid = %s",
                        (
                            utc_now(),
                            sanitize_error_message(str(error)),
                            run["processing_run_uuid"],
                        ),
                    )
            raise

    @staticmethod
    def _validate_prepared_inference(
        prepared: PreparedJakobsonInference,
        *,
        message_uuid: str,
        model_role: str,
        model_profile_uuid: str | None,
        provider_type: str,
        model_name: str,
    ) -> None:
        expected = (
            message_uuid,
            model_role,
            model_profile_uuid,
            provider_type,
            model_name,
        )
        actual = (
            prepared.message_uuid,
            prepared.model_role,
            prepared.model_profile_uuid,
            prepared.provider_type,
            prepared.model_name,
        )
        if actual != expected:
            raise RuntimeError("prepared inference execution identity mismatch")

    def _resolve_profile(self, message: dict[str, Any]) -> dict[str, Any] | None:
        repository = PostgresModelControlRepository(self.connection)
        resolution = RoleResolutionService(repository).resolve(
            ModelRole.MEMORY_EXTRACTION,
            workspace_uuid=(
                str(message["workspace_uuid"]) if message.get("workspace_uuid") else None
            ),
            project_uuid=(str(message["project_uuid"]) if message.get("project_uuid") else None),
        )
        if resolution.model_profile_uuid:
            profile = repository.get_profile(resolution.model_profile_uuid)
            if profile is not None:
                values = profile.model_dump(mode="json")
                values.update(
                    {
                        "model_role": resolution.requested_role.value,
                        "requested_role": resolution.requested_role.value,
                        "effective_role": resolution.effective_role.value,
                        "scope_source": resolution.scope_source,
                        "inheritance_source": resolution.inheritance_source,
                    }
                )
                return values
        return resolution.runtime_profile()

    def _get_or_create_run(
        self,
        message: dict[str, Any],
        identity: Any,
        model_profile_uuid: str | None,
        provider_type: str,
        model_name: str,
    ) -> dict[str, Any]:
        existing = self.connection.execute(
            """
            SELECT * FROM memory_processing_runs
            WHERE message_uuid = %s AND pipeline_version = %s AND prompt_bundle_version = %s
              AND input_content_hash = %s AND COALESCE(model_profile_uuid, '') = COALESCE(%s, '')
              AND COALESCE(provider_type, '') = COALESCE(%s, '')
              AND COALESCE(model_role, '') = COALESCE(%s, '')
              AND COALESCE(model_name, '') = COALESCE(%s, '')
              AND COALESCE(prompt_id, '') = COALESCE(%s, '')
              AND COALESCE(prompt_version, '') = COALESCE(%s, '')
              AND COALESCE(processing_identity_hash, '') = COALESCE(%s, '')
            ORDER BY created_at LIMIT 1
            """,
            (
                message["message_uuid"],
                identity.pipeline_version,
                identity.prompt_bundle_version,
                identity.input_content_hash,
                model_profile_uuid,
                provider_type,
                identity.model_role,
                model_name,
                identity.prompt_id,
                identity.prompt_version,
                identity.identity_hash,
            ),
        ).fetchone()
        if existing is not None:
            return dict(existing)
        run_uuid = new_uuid()
        self.connection.execute(
            """
            INSERT INTO memory_processing_runs (processing_run_uuid, session_uuid, message_uuid, pipeline_version, prompt_bundle_version,
              input_content_hash, status, created_at, schema_version, model_profile_uuid, provider_type, model_role, model_name, processing_identity_hash, input_hash)
            VALUES (%s, %s, %s, %s, %s, %s, 'pending', %s, 1, %s, %s, %s, %s, %s, %s)
            """,
            (
                run_uuid,
                message["session_uuid"],
                message["message_uuid"],
                identity.pipeline_version,
                identity.prompt_bundle_version,
                identity.input_content_hash,
                utc_now(),
                model_profile_uuid,
                provider_type,
                identity.model_role,
                model_name,
                identity.identity_hash,
                identity.input_content_hash,
            ),
        )
        self.connection.execute(
            "UPDATE memory_processing_runs SET prompt_id = %s, prompt_version = %s WHERE processing_run_uuid = %s",
            (identity.prompt_id, identity.prompt_version, run_uuid),
        )
        return dict(
            self.connection.execute(
                "SELECT * FROM memory_processing_runs WHERE processing_run_uuid = %s", (run_uuid,)
            ).fetchone()
        )

    def _unitize(self, message: dict[str, Any], raw_text: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT * FROM text_units WHERE message_uuid = %s ORDER BY unit_index",
            (message["message_uuid"],),
        ).fetchall()
        if rows:
            return [dict(row) for row in rows]
        units = self.segmenter.to_text_units(
            message_uuid=message["message_uuid"],
            session_uuid=message["session_uuid"],
            speaker_role=message["role"],
            text=raw_text,
        )
        for unit in units:
            self.connection.execute(
                """
                INSERT INTO text_units (text_unit_uuid, unit_uuid, message_uuid, session_uuid, speaker_role, unit_type, unit_index, text,
                  start_char, end_char, char_start, char_end, language_code, language_hint, segmentation_confidence, segmentation_notes,
                  content_hash, created_at, schema_version)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,1)
                ON CONFLICT (message_uuid, unit_type, unit_index) DO NOTHING
                """,
                (
                    unit.text_unit_uuid,
                    unit.unit_uuid,
                    unit.message_uuid,
                    unit.session_uuid,
                    unit.speaker_role,
                    unit.unit_type.value,
                    unit.unit_index,
                    unit.text,
                    unit.start_char,
                    unit.end_char,
                    unit.char_start,
                    unit.char_end,
                    unit.language_code,
                    unit.language_hint,
                    unit.segmentation_confidence,
                    unit.segmentation_notes,
                    unit.content_hash,
                    unit.created_at,
                ),
            )
        return [
            dict(row)
            for row in self.connection.execute(
                "SELECT * FROM text_units WHERE message_uuid = %s ORDER BY unit_index",
                (message["message_uuid"],),
            ).fetchall()
        ]

    def _run_model_prompt(
        self,
        profile: dict[str, Any],
        input_payload: dict[str, Any],
        message: dict[str, Any],
        model_name: str,
        provider_type: str,
        model_role: str,
        import_run_uuid: str | None,
        job_uuid: str | None,
        lease_fence: Callable[[], None] | None = None,
    ) -> tuple[dict[str, Any], str, dict[str, int]]:
        prompt = render_prompt(
            JAKOBSON_SENTENCE_ANALYSIS_PROMPT_ID, PROMPT_PACK_VERSION, input_payload
        )
        timeout_ms = int(
            profile.get("timeout_ms")
            or self.settings.processing_timeout_ms("memory_extraction")
        )
        provider = OpenAICompatibleMemoryExtractionProvider.from_profile(
            profile, timeout_ms=timeout_ms
        )
        response = provider.extract(system_prompt=prompt, input_payload=input_payload)
        validate_prompt_execution(
            JAKOBSON_SENTENCE_ANALYSIS_PROMPT_ID,
            PROMPT_PACK_VERSION,
            input_payload,
            response.output,
        )
        if lease_fence is not None:
            lease_fence()
        prompt_execution_uuid = self._record_prompt_execution(
            input_payload=input_payload,
            output=response.output,
            message=message,
            model_profile_uuid=str(profile["model_profile_uuid"]),
            provider_type=provider_type,
            model_name=model_name,
            model_role=model_role,
            latency_ms=response.latency_ms,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            import_run_uuid=import_run_uuid,
            job_uuid=job_uuid,
        )
        return (
            response.output,
            prompt_execution_uuid,
            {
                "input_tokens": response.input_tokens,
                "output_tokens": response.output_tokens,
                "latency_ms": response.latency_ms,
            },
        )

    def _record_prompt_execution(
        self,
        *,
        input_payload: dict[str, Any],
        output: dict[str, Any],
        message: dict[str, Any],
        model_profile_uuid: str | None,
        provider_type: str,
        model_name: str,
        model_role: str,
        latency_ms: int,
        input_tokens: int,
        output_tokens: int,
        import_run_uuid: str | None,
        job_uuid: str | None,
        prompt_version: str = JAKOBSON_SENTENCE_ANALYSIS_ACTIVE_VERSION,
    ) -> str:
        validate_prompt_execution(
            JAKOBSON_SENTENCE_ANALYSIS_PROMPT_ID, prompt_version, input_payload, output
        )
        prompt_execution_uuid = new_uuid()
        row = {
            "prompt_execution_uuid": prompt_execution_uuid,
            "prompt_id": JAKOBSON_SENTENCE_ANALYSIS_PROMPT_ID,
            "prompt_version": prompt_version,
            "stage": "jakobson_sentence_analysis",
            "model_profile_uuid": model_profile_uuid,
            "model_role": model_role,
            "provider_type": provider_type,
            "model_name": model_name,
            "workspace_uuid": message.get("workspace_uuid"),
            "project_uuid": message.get("project_uuid"),
            "session_uuid": message["session_uuid"],
            "message_uuid": message["message_uuid"],
            "import_run_uuid": import_run_uuid,
            "job_uuid": job_uuid,
            "input_hash": canonical_hash_ijson(input_payload),
            "output_hash": canonical_hash_ijson(output),
            "raw_output_ijson": json.dumps(output, sort_keys=True),
            "validated_output_ijson": json.dumps(output, sort_keys=True),
            "status": "ok",
            "warnings_ijson": json.dumps(output.get("warnings", []), sort_keys=True),
            "error_sanitized": None,
            "latency_ms": latency_ms,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "created_at": utc_now(),
            "schema_version": 1,
        }
        columns = list(row)
        placeholders = ",".join(["%s"] * len(columns))
        self.connection.execute(
            f"INSERT INTO prompt_execution_runs ({', '.join(columns)}) VALUES ({placeholders})",
            tuple(row[column] for column in columns),
        )
        return prompt_execution_uuid

    def _record_extraction_stage(
        self,
        prepared: PreparedJakobsonInference,
        *,
        processing_run_uuid: str,
        message: dict[str, Any],
        input_payload: dict[str, Any],
        output: dict[str, Any],
        model_profile_uuid: str | None,
        provider_type: str,
        model_name: str,
        model_role: str,
        prompt_version: str,
    ) -> None:
        """Persist the truthful memory-extraction provider attempt.

        This closes the audit gap: even when the remote provider produced
        invalid output and the deterministic fallback was used, a
        processing_stage_runs row exists with the exact attempt/repair/fallback
        state. The row is written inside the fenced write with the validated
        (provider or fallback) output, so it never disappears.
        """

        status = stage_status_for_output(
            output.get("status"),
            fallback_used=prepared.fallback_used,
            provider_failed=not prepared.provider_output_valid,
        ).value
        row = {
            "stage_execution_uuid": prepared.stage_execution_uuid or new_uuid(),
            "processing_run_uuid": processing_run_uuid,
            "job_uuid": prepared.job_uuid,
            "source_type": "message",
            "source_uuid": str(message["message_uuid"]),
            "requested_role": prepared.requested_role,
            "effective_role": prepared.effective_role,
            "stage": "jakobson_sentence_analysis",
            "model_profile_uuid": model_profile_uuid,
            "provider_type": provider_type,
            "model_name": model_name,
            "prompt_id": JAKOBSON_SENTENCE_ANALYSIS_PROMPT_ID,
            "prompt_version": prompt_version,
            "contract_hash": prepared.contract_hash,
            "profile_fingerprint": prepared.profile_fingerprint,
            "input_hash": canonical_hash_ijson(input_payload),
            "output_hash": canonical_hash_ijson(output),
            "status": status,
            "called_provider": prepared.called_provider,
            "fallback_used": prepared.fallback_used,
            "scope_source": prepared.scope_source,
            "inheritance_source": prepared.inheritance_source,
            "fallback_reason": prepared.fallback_reason,
            "detail_sanitized": None,
            "validation_errors_jsonb": json.dumps(prepared.validation_error_paths, sort_keys=True),
            "input_tokens": prepared.input_tokens,
            "output_tokens": prepared.output_tokens,
            "embedding_count": 0,
            "latency_ms": prepared.latency_ms,
            "idempotency_key": f"jakobson:{processing_run_uuid}:{message['message_uuid']}",
            "created_at": utc_now(),
            "completed_at": utc_now(),
            "schema_version": 1,
            "provider_output_valid": prepared.provider_output_valid,
            "repair_attempted": prepared.repair_attempted,
            "repair_succeeded": prepared.repair_succeeded,
            "parse_status": prepared.parse_status,
            "capability_mode": prepared.capability_mode,
            "provider_response_id": prepared.provider_response_id,
            "canonicalized": prepared.canonicalized,
            "attempt_count": prepared.attempt_count,
            "total_provider_latency_ms": prepared.latency_ms,
        }
        columns = list(row)
        placeholders = ",".join(
            "%s::jsonb" if column == "validation_errors_jsonb" else "%s" for column in columns
        )
        self.connection.execute(
            f"INSERT INTO processing_stage_runs ({', '.join(columns)}) VALUES ({placeholders}) "
            "ON CONFLICT (idempotency_key) DO UPDATE SET "
            + ", ".join(
                f"{column} = excluded.{column}"
                for column in columns
                if column not in {"stage_execution_uuid", "idempotency_key", "created_at"}
            ),
            tuple(row[column] for column in columns),
        )

    def _record_usage(
        self,
        message: dict[str, Any],
        model_profile_uuid: str | None,
        provider_type: str,
        model_name: str,
        model_role: str,
        import_run_uuid: str | None,
        job_uuid: str | None,
        usage: dict[str, int],
        *,
        status: str,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO model_usage_events (
              usage_event_uuid, model_profile_uuid, role, event_type, input_tokens,
              output_tokens, created_at, schema_version, stage, provider_type,
              model_name, workspace_uuid, project_uuid, session_uuid, message_uuid,
              import_run_uuid, job_uuid, latency_ms, status
            )
            VALUES (
              %s,%s,%s,'prompt_execution',%s,%s,%s,1,'jakobson_sentence_analysis',
              %s,%s,%s,%s,%s,%s,%s,%s,%s,%s
            )
            """,
            (
                new_uuid(),
                model_profile_uuid,
                model_role,
                int(usage.get("input_tokens", 0)),
                int(usage.get("output_tokens", 0)),
                utc_now(),
                provider_type,
                model_name,
                message.get("workspace_uuid"),
                message.get("project_uuid"),
                message["session_uuid"],
                message["message_uuid"],
                import_run_uuid,
                job_uuid,
                int(usage.get("latency_ms", 0)),
                status,
            ),
        )

    def _record_jakobson(
        self,
        message: dict[str, Any],
        input_payload: dict[str, Any],
        output: dict[str, Any],
        model_profile_uuid: str | None,
        provider_type: str,
        model_name: str,
        prompt_execution_uuid: str,
        prompt_version: str,
    ) -> str:
        existing = self.connection.execute(
            "SELECT analysis_run_uuid FROM jakobson_analysis_runs WHERE message_uuid = %s AND prompt_execution_uuid = %s",
            (message["message_uuid"], prompt_execution_uuid),
        ).fetchone()
        if existing:
            return str(existing["analysis_run_uuid"])
        analysis_run_uuid = new_uuid()
        self.connection.execute(
            """
            INSERT INTO jakobson_analysis_runs (analysis_run_uuid, workspace_uuid, project_uuid, session_uuid, message_uuid, prompt_id, prompt_version,
              model_profile_uuid, provider_type, model_name, input_hash, output_hash, status, warnings_jsonb, raw_output_jsonb, created_at, schema_version, prompt_execution_uuid)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'succeeded',%s::jsonb,%s::jsonb,%s,1,%s)
            """,
            (
                analysis_run_uuid,
                message.get("workspace_uuid"),
                message.get("project_uuid"),
                message["session_uuid"],
                message["message_uuid"],
                JAKOBSON_SENTENCE_ANALYSIS_PROMPT_ID,
                prompt_version,
                model_profile_uuid,
                provider_type,
                model_name,
                canonical_hash_ijson(input_payload),
                canonical_hash_ijson(output),
                json.dumps(output.get("warnings", [])),
                json.dumps(output),
                utc_now(),
                prompt_execution_uuid,
            ),
        )
        return analysis_run_uuid

    def _record_annotations(
        self,
        message_uuid: str,
        analysis_run_uuid: str,
        units: list[dict[str, Any]],
        output: dict[str, Any],
    ) -> list[dict[str, Any]]:
        for idx, sentence in enumerate(canonical_sentence_items(output)):
            unit = units[min(idx, len(units) - 1)]
            factors = sentence["six_factors"]
            annotation_uuid = new_uuid()
            self.connection.execute(
                """
                INSERT INTO jakobson_sentence_annotations (annotation_uuid, analysis_run_uuid, message_uuid, unit_uuid, sentence_index, sentence_text,
                  sentence_hash, sender_value, sender_evidence, sender_confidence, receiver_value, receiver_evidence, receiver_confidence, message_value,
                  message_evidence, message_confidence, context_value, context_evidence, context_confidence, code_value, code_evidence, code_confidence,
                  contact_channel_value, contact_channel_evidence, contact_channel_confidence, dominant_function, secondary_functions_jsonb, function_reason,
                  notes, raw_sentence_output_jsonb, created_at, schema_version)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s::jsonb,%s,1)
                ON CONFLICT (analysis_run_uuid, unit_uuid) DO NOTHING
                """,
                (
                    annotation_uuid,
                    analysis_run_uuid,
                    message_uuid,
                    unit["text_unit_uuid"],
                    idx + 1,
                    sentence["text"],
                    sha256(sentence["text"].encode()).hexdigest(),
                    factors["sender_addresser"].get("value"),
                    factors["sender_addresser"].get("evidence"),
                    factors["sender_addresser"].get("confidence", "medium"),
                    factors["receiver_addressee"].get("value"),
                    factors["receiver_addressee"].get("evidence"),
                    factors["receiver_addressee"].get("confidence", "medium"),
                    factors["message"].get("value"),
                    factors["message"].get("evidence"),
                    factors["message"].get("confidence", "medium"),
                    factors["context_referent"].get("value"),
                    factors["context_referent"].get("evidence"),
                    factors["context_referent"].get("confidence", "medium"),
                    factors["code"].get("value"),
                    factors["code"].get("evidence"),
                    factors["code"].get("confidence", "medium"),
                    factors["contact_channel"].get("value"),
                    factors["contact_channel"].get("evidence"),
                    factors["contact_channel"].get("confidence", "medium"),
                    sentence["dominant_function"],
                    json.dumps(sentence.get("secondary_functions", [])),
                    sentence.get("function_reason"),
                    sentence.get("notes"),
                    json.dumps(sentence),
                    utc_now(),
                ),
            )
        return [
            dict(row)
            for row in self.connection.execute(
                "SELECT * FROM jakobson_sentence_annotations WHERE analysis_run_uuid = %s ORDER BY sentence_index",
                (analysis_run_uuid,),
            ).fetchall()
        ]

    def _record_routes(
        self, message_uuid: str, annotations: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        return record_routes(self, message_uuid, annotations)

    def _record_gates(
        self, processing_run_uuid: str, units: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        for unit in units:
            existing = self.connection.execute(
                "SELECT 1 FROM memory_gate_decisions WHERE processing_run_uuid = %s AND text_unit_uuid = %s",
                (processing_run_uuid, unit["text_unit_uuid"]),
            ).fetchone()
            if existing:
                continue
            decision = self.gate.evaluate(
                cast(
                    Any,
                    SimpleNamespace(text=unit["text"], speaker_role=unit["speaker_role"]),
                )
            )
            self.connection.execute(
                """
                INSERT INTO memory_gate_decisions (gate_decision_uuid, text_unit_uuid, processing_run_uuid, decision, reason_codes_ijson, salience_score,
                  persistence_score, actionability_score, sensitivity_score, novelty_score, requires_high_confidence_pass, created_at, schema_version)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,1)
                """,
                (
                    new_uuid(),
                    unit["text_unit_uuid"],
                    processing_run_uuid,
                    decision.decision.value,
                    json.dumps(decision.reason_codes),
                    decision.salience_score,
                    decision.persistence_score,
                    decision.actionability_score,
                    decision.sensitivity_score,
                    decision.novelty_score,
                    decision.requires_high_confidence_pass,
                    utc_now(),
                ),
            )
        return [
            dict(row)
            for row in self.connection.execute(
                "SELECT * FROM memory_gate_decisions WHERE processing_run_uuid = %s",
                (processing_run_uuid,),
            ).fetchall()
        ]

    def _record_linguistic_analyses(
        self, processing_run_uuid: str, units: list[dict[str, Any]], output: dict[str, Any]
    ) -> list[dict[str, Any]]:
        sentences = canonical_sentence_items(output)
        for idx, unit in enumerate(units):
            existing = self.connection.execute(
                "SELECT 1 FROM linguistic_analyses WHERE processing_run_uuid = %s AND text_unit_uuid = %s",
                (processing_run_uuid, unit["text_unit_uuid"]),
            ).fetchone()
            if existing:
                continue
            sentence = sentences[idx] if idx < len(sentences) else {}
            raw = json.dumps(sentence, sort_keys=True)
            self.connection.execute(
                """
                INSERT INTO linguistic_analyses (analysis_uuid, text_unit_uuid, processing_run_uuid, analysis_schema_version, speech_acts_ijson,
                  jakobson_functions_ijson, conceptual_nuclei_ijson, entity_mentions_ijson, temporal_expressions_ijson, modality_ijson,
                  memory_signals_ijson, abstention_ijson, raw_output_ijson, created_at, schema_version)
                VALUES (%s,%s,%s,'prompt-pack-v2',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,1)
                """,
                (
                    new_uuid(),
                    unit["text_unit_uuid"],
                    processing_run_uuid,
                    json.dumps([]),
                    json.dumps(
                        {
                            "dominant_function": sentence.get("dominant_function"),
                            "secondary_functions": sentence.get("secondary_functions", []),
                        }
                    ),
                    json.dumps(
                        [sentence.get("six_factors", {}).get("context_referent", {}).get("value")]
                    ),
                    json.dumps([]),
                    json.dumps([]),
                    # Full previously stored an empty modality, so it could never
                    # agree with Lite on polarity. Both runtimes now derive it
                    # from the same shared extractor.
                    json.dumps(modality_payload(str(unit.get("text") or ""))),
                    json.dumps({"memory_signal": "medium"}),
                    json.dumps({"abstained": False}),
                    raw,
                    utc_now(),
                ),
            )
        return [
            dict(row)
            for row in self.connection.execute(
                "SELECT * FROM linguistic_analyses WHERE processing_run_uuid = %s",
                (processing_run_uuid,),
            ).fetchall()
        ]

    def _record_candidates(
        self,
        processing_run_uuid: str,
        message: dict[str, Any],
        units: list[dict[str, Any]],
        annotations: list[dict[str, Any]],
        routes: list[dict[str, Any]],
        prompt_execution_uuid: str,
        provider_type: str,
        import_run_uuid: str | None = None,
        model_name: str | None = None,
        analyses: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        return record_candidates(
            self,
            processing_run_uuid,
            message,
            units,
            annotations,
            routes,
            prompt_execution_uuid,
            provider_type,
            import_run_uuid,
            model_name,
            analyses,
        )

    def _semantic_candidate_rows(self, candidate_uuids: list[str]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for candidate_uuid in candidate_uuids:
            row = self.connection.execute(
                "SELECT * FROM memory_candidates WHERE candidate_uuid = %s",
                (candidate_uuid,),
            ).fetchone()
            if row is None:
                raise RuntimeError("semantic candidate link did not persist its candidate")
            rows.append(dict(row))
        return rows

    def _run_candidate_stages(
        self,
        processing_run_uuid: str,
        message: dict[str, Any],
        candidates: list[dict[str, Any]],
        *,
        lease_fence: Callable[..., None] | None = None,
    ) -> list[dict[str, Any]]:
        repository = PostgresModelControlRepository(self.connection)
        invoker = StageInvoker(self.connection, repository, postgres=True)
        results: list[dict[str, Any]] = []
        for candidate in candidates:
            evidence = self.connection.execute(
                "SELECT evidence_text FROM candidate_evidence "
                "WHERE candidate_uuid = %s ORDER BY created_at LIMIT 1",
                (candidate["candidate_uuid"],),
            ).fetchone()
            evidence_text = (
                str(evidence["evidence_text"])
                if evidence is not None
                else str(candidate["normalized_text"])
            )
            self.connection.commit()
            privacy = invoker.invoke_structured(
                StageInvocationRequest(
                    role=ModelRole.PRIVACY_SENSITIVITY,
                    stage="privacy_sensitivity",
                    source_type="memory_candidate",
                    source_uuid=str(candidate["candidate_uuid"]),
                    processing_run_uuid=processing_run_uuid,
                    workspace_uuid=_optional_text(message.get("workspace_uuid")),
                    project_uuid=_optional_text(message.get("project_uuid")),
                    session_uuid=str(message["session_uuid"]),
                    message_uuid=str(message["message_uuid"]),
                    prompt_id="memorist.privacy_sensitivity",
                    prompt_version="2.0",
                    input_payload={
                        "candidate_text": str(candidate["normalized_text"]),
                        "evidence_text": evidence_text,
                        "source_authority": str(candidate.get("source_authority") or "unknown"),
                    },
                ),
                validator=validate_privacy_result,
                deterministic_output=deterministic_privacy,
                lease_fence=lease_fence,
            )
            classification = str((privacy.output or {}).get("classification") or "abstain")
            status = str(candidate["status"])
            sensitivity = str(candidate.get("sensitivity") or "normal")
            if classification == "secret":
                status, sensitivity = "rejected", "secret"
            elif classification in {"sensitive", "requires_review", "abstain"}:
                if status != "rejected":
                    status = "needs_review"
                if classification == "sensitive":
                    sensitivity = "sensitive"
            with fenced_write(self.connection, lease_fence, postgres=True):
                self.connection.execute(
                    "UPDATE memory_candidates SET status = %s, sensitivity = %s "
                    "WHERE candidate_uuid = %s",
                    (status, sensitivity, candidate["candidate_uuid"]),
                )
            candidate["status"] = status
            candidate["sensitivity"] = sensitivity
            results.append(privacy.model_dump(mode="json", exclude={"output"}))

            metadata = _json_mapping(candidate.get("extraction_metadata_jsonb"))
            if not bool(metadata.get("requires_high_confidence_pass")):
                continue
            self.connection.commit()
            high = invoker.invoke_structured(
                StageInvocationRequest(
                    role=ModelRole.HIGH_CONFIDENCE_EXTRACTION,
                    stage="high_confidence_extraction",
                    source_type="memory_candidate",
                    source_uuid=str(candidate["candidate_uuid"]),
                    processing_run_uuid=processing_run_uuid,
                    workspace_uuid=_optional_text(message.get("workspace_uuid")),
                    project_uuid=_optional_text(message.get("project_uuid")),
                    session_uuid=str(message["session_uuid"]),
                    message_uuid=str(message["message_uuid"]),
                    prompt_id="memorist.high_confidence_extraction",
                    prompt_version="1.0",
                    input_payload={
                        "candidate_text": str(candidate["normalized_text"]),
                        "evidence_text": evidence_text,
                        "source_authority": str(candidate.get("source_authority") or "unknown"),
                        "route_type": metadata.get("route_type"),
                    },
                ),
                validator=validate_high_confidence_result,
                deterministic_output=deterministic_high_confidence,
                lease_fence=lease_fence,
            )
            decision = str((high.output or {}).get("decision") or "abstain")
            if decision == "rejected":
                status = "rejected"
            elif decision in {"needs_review", "abstain"} and status != "rejected":
                status = "needs_review"
            metadata["high_confidence_stage_status"] = decision
            metadata["high_confidence_execution_uuid"] = high.execution_uuid
            with fenced_write(self.connection, lease_fence, postgres=True):
                self.connection.execute(
                    "UPDATE memory_candidates SET status = %s, "
                    "extraction_metadata_jsonb = %s::jsonb "
                    "WHERE candidate_uuid = %s",
                    (
                        status,
                        json.dumps(metadata, sort_keys=True),
                        candidate["candidate_uuid"],
                    ),
                )
            candidate["status"] = status
            candidate["extraction_metadata_jsonb"] = metadata
            results.append(high.model_dump(mode="json", exclude={"output"}))
        return results

    def _run_embedding_stages(
        self,
        processing_run_uuid: str,
        message: dict[str, Any],
        candidates: list[dict[str, Any]],
        *,
        lease_fence: Callable[..., None] | None = None,
    ) -> list[dict[str, Any]]:
        repository = PostgresModelControlRepository(self.connection)
        invoker = StageInvoker(self.connection, repository, postgres=True)
        resolution = invoker.resolver.resolve(
            ModelRole.EMBEDDING,
            workspace_uuid=_optional_text(message.get("workspace_uuid")),
            project_uuid=_optional_text(message.get("project_uuid")),
        )
        rows = self.connection.execute(
            """
            SELECT mv.memory_version_uuid, mv.memory_uuid, mv.normalized_text
            FROM memory_versions mv
            JOIN memory_evidence_links mel
              ON mel.memory_version_uuid = mv.memory_version_uuid
            JOIN memory_candidates mc ON mc.candidate_uuid = mel.candidate_uuid
            WHERE mc.processing_run_uuid = %s
            ORDER BY mv.created_at
            """,
            (processing_run_uuid,),
        ).fetchall()
        profile = (
            repository.get_profile(resolution.model_profile_uuid)
            if resolution.model_profile_uuid
            else None
        )
        expected_dimension = profile.embedding_dimension if profile else None
        results: list[dict[str, Any]] = []
        for row in rows:
            version_uuid = str(row["memory_version_uuid"])
            text = str(row["normalized_text"])
            content_hash = sha256(text.encode("utf-8")).hexdigest()
            payload = json.dumps(
                {
                    "memory_uuid": row["memory_uuid"],
                    "memory_version_uuid": version_uuid,
                    "content_hash": content_hash,
                },
                sort_keys=True,
            )
            with fenced_write(self.connection, lease_fence, postgres=True):
                self.connection.execute(
                    """
                    INSERT INTO embedding_outbox (
                        outbox_uuid, event_type, source_type, source_uuid, payload_jsonb,
                        status, priority, attempts, run_after, created_at, updated_at,
                        schema_version
                    ) VALUES (%s,'memory_version_upserted','memory_version',%s,%s::jsonb,
                              'pending',25,0,%s,%s,%s,1)
                    ON CONFLICT (event_type, source_type, source_uuid) DO NOTHING
                    """,
                    (new_uuid(), version_uuid, payload, utc_now(), utc_now(), utc_now()),
                )
            # A replayed stage returns no vectors, so only accept a replay when
            # the projection row from the first execution actually exists.
            projection_sql = (
                "SELECT 1 FROM memory_version_embeddings "
                "WHERE memory_version_uuid = %s AND content_hash = %s "
                "AND embedding_model = %s"
            )
            projection_params: tuple[object, ...] = (
                version_uuid,
                content_hash,
                resolution.model_name,
            )
            if expected_dimension is not None:
                projection_sql += " AND embedding_dimension = %s"
                projection_params += (expected_dimension,)
            projection_exists = (
                self.connection.execute(projection_sql, projection_params).fetchone() is not None
            )
            if projection_exists:
                attempt_row = self.connection.execute(
                    "SELECT attempts FROM embedding_outbox "
                    "WHERE event_type = 'memory_version_upserted' AND source_uuid = %s",
                    (version_uuid,),
                ).fetchone()
                projection_attempt = int(attempt_row["attempts"] if attempt_row else 1)
            else:
                with fenced_write(self.connection, lease_fence, postgres=True):
                    attempt_row = self.connection.execute(
                        """
                        UPDATE embedding_outbox
                        SET status = 'running', attempts = attempts + 1,
                            last_error_sanitized = NULL, updated_at = %s
                        WHERE event_type = 'memory_version_upserted' AND source_uuid = %s
                        RETURNING attempts
                        """,
                        (utc_now(), version_uuid),
                    ).fetchone()
                if attempt_row is None:
                    raise RuntimeError("embedding outbox attempt could not be claimed")
                projection_attempt = int(attempt_row["attempts"])
            result, vectors = invoker.invoke_embedding(
                StageInvocationRequest(
                    role=ModelRole.EMBEDDING,
                    stage="embedding_generation",
                    source_type="memory_version",
                    source_uuid=version_uuid,
                    processing_run_uuid=processing_run_uuid,
                    workspace_uuid=_optional_text(message.get("workspace_uuid")),
                    project_uuid=_optional_text(message.get("project_uuid")),
                    session_uuid=str(message["session_uuid"]),
                    message_uuid=str(message["message_uuid"]),
                    prompt_version="1.0",
                    input_payload={
                        "text": text,
                        "content_hash": content_hash,
                        "projection_attempt": projection_attempt,
                    },
                ),
                texts=[text],
                expected_dimension=expected_dimension,
                allow_replay=projection_exists,
                lease_fence=lease_fence,
            )
            if vectors and result.model_profile_uuid:
                vector = vectors[0]
                with fenced_write(self.connection, lease_fence, postgres=True):
                    self.connection.execute(
                        """
                        INSERT INTO memory_version_embeddings (
                            memory_version_uuid, embedding_model, embedding_dimension,
                            embedding_version, content_hash, embedding_jsonb, created_at,
                            schema_version
                        ) VALUES (%s,%s,%s,'1',%s,%s::jsonb,%s,1)
                        ON CONFLICT (memory_version_uuid, embedding_model, embedding_version)
                        DO UPDATE SET content_hash = excluded.content_hash,
                                      embedding_dimension = excluded.embedding_dimension,
                                      embedding_jsonb = excluded.embedding_jsonb,
                                      created_at = excluded.created_at
                        """,
                        (
                            version_uuid,
                            result.model_name,
                            len(vector),
                            content_hash,
                            json.dumps(vector),
                            utc_now(),
                        ),
                    )
                    repository.record_embedding(
                        result.model_profile_uuid,
                        "memory_version",
                        version_uuid,
                        content_hash,
                        f"postgres:memory_version_embeddings:{version_uuid}",
                        len(vector),
                    )
                    self.connection.execute(
                        "UPDATE embedding_outbox SET status = 'succeeded', updated_at = %s "
                        "WHERE event_type = 'memory_version_upserted' AND source_uuid = %s",
                        (utc_now(), version_uuid),
                    )
            elif projection_exists:
                with fenced_write(self.connection, lease_fence, postgres=True):
                    self.connection.execute(
                        "UPDATE embedding_outbox SET status = 'succeeded', updated_at = %s "
                        "WHERE event_type = 'memory_version_upserted' AND source_uuid = %s",
                        (utc_now(), version_uuid),
                    )
            else:
                with fenced_write(self.connection, lease_fence, postgres=True):
                    self.connection.execute(
                        "UPDATE embedding_outbox SET status = %s, "
                        "last_error_sanitized = %s, updated_at = %s "
                        "WHERE event_type = 'memory_version_upserted' AND source_uuid = %s",
                        (
                            "skipped" if result.status == "abstained" else "pending",
                            result.detail_sanitized,
                            utc_now(),
                            version_uuid,
                        ),
                    )
            results.append(result.model_dump(mode="json", exclude={"output"}))
        return results

    def _record_memories(
        self,
        message: dict[str, Any],
        candidates: list[dict[str, Any]],
        content_hash: str,
        prompt_execution_uuid: str,
    ) -> int:
        created = 0
        scope_type, scope_uuid = _scope_for_message(message)
        for candidate in candidates:
            if str(candidate.get("status")) != "accepted":
                continue
            memory_uuid = new_uuid()
            version_uuid = new_uuid()
            canonical_key = (
                f"message:{message['message_uuid']}:candidate:{candidate['candidate_uuid']}"
            )
            existing = self.connection.execute(
                "SELECT memory_uuid FROM memories WHERE scope_type = %s AND scope_uuid = %s AND canonical_key = %s",
                (scope_type, scope_uuid, canonical_key),
            ).fetchone()
            if existing:
                continue
            self.connection.execute(
                "INSERT INTO memories (memory_uuid, scope_type, scope_uuid, canonical_key, current_version_uuid, status, created_at, updated_at, schema_version) VALUES (%s,%s,%s,%s,%s,'active',%s,%s,1)",
                (
                    memory_uuid,
                    scope_type,
                    scope_uuid,
                    canonical_key,
                    version_uuid,
                    utc_now(),
                    utc_now(),
                ),
            )
            self.connection.execute(
                "INSERT INTO memory_versions (memory_version_uuid, memory_uuid, version_number, operation, value, normalized_text, confidence, importance, source_snapshot_hash, transaction_from, valid_from, status, created_at, schema_version, prompt_execution_uuid, source_candidate_uuid, polarity) VALUES (%s,%s,1,'create',%s,%s,%s,%s,%s,%s,%s,'current',%s,1,%s,%s,%s)",
                (
                    version_uuid,
                    memory_uuid,
                    candidate["normalized_text"],
                    candidate["normalized_text"],
                    candidate["confidence"],
                    candidate["importance"],
                    content_hash,
                    utc_now(),
                    utc_now(),
                    utc_now(),
                    prompt_execution_uuid,
                    candidate["candidate_uuid"],
                    # The durable record must carry the same polarity as the
                    # candidate it was consolidated from; Lite does this in
                    # consolidation/consolidator.py.
                    coerce_polarity(candidate.get("polarity")).value,
                ),
            )
            evidence = self.connection.execute(
                "SELECT * FROM candidate_evidence WHERE candidate_uuid = %s ORDER BY created_at LIMIT 1",
                (candidate["candidate_uuid"],),
            ).fetchone()
            if evidence:
                self.connection.execute(
                    "INSERT INTO memory_evidence_links (link_uuid, memory_uuid, memory_version_uuid, candidate_uuid, evidence_uuid, created_at, schema_version) VALUES (%s,%s,%s,%s,%s,%s,1) ON CONFLICT (memory_version_uuid, evidence_uuid) DO NOTHING",
                    (
                        new_uuid(),
                        memory_uuid,
                        version_uuid,
                        candidate["candidate_uuid"],
                        evidence["evidence_uuid"],
                        utc_now(),
                    ),
                )
            payload = json.dumps(
                {
                    "memory_uuid": memory_uuid,
                    "memory_version_uuid": version_uuid,
                    "operation": "create",
                }
            )
            self.connection.execute(
                "INSERT INTO graph_projection_outbox (outbox_uuid, event_type, source_type, source_uuid, payload_jsonb, status, priority, attempts, run_after, created_at, updated_at, schema_version) VALUES (%s,'memory_upserted','memory',%s,%s::jsonb,'pending',50,0,%s,%s,%s,1) ON CONFLICT (event_type, source_type, source_uuid) DO NOTHING",
                (new_uuid(), memory_uuid, payload, utc_now(), utc_now(), utc_now()),
            )
            created += 1
        return created

    def _deterministic_jakobson_output(self, units: list[dict[str, Any]]) -> dict[str, Any]:
        return deterministic_jakobson_output(self, units)

    def _summary(
        self, message_uuid: str, processing_run_uuid: str, replay: bool
    ) -> dict[str, object]:
        return {
            "message_uuid": message_uuid,
            "processing_run_uuid": processing_run_uuid,
            "pipeline_version": PIPELINE_VERSION,
            "prompt_bundle_version": PROMPT_BUNDLE_VERSION,
            "idempotent_replay": replay,
            "text_units": self._count("text_units", "message_uuid", message_uuid),
            "memory_candidates": self._count(
                "memory_candidates", "processing_run_uuid", processing_run_uuid
            ),
            "memories": self._count_memories(processing_run_uuid),
            "status": "succeeded",
        }

    def _count(self, table: str, column: str, value: str) -> int:
        return int(
            self.connection.execute(
                f"SELECT COUNT(*) AS count FROM {table} WHERE {column} = %s", (value,)
            ).fetchone()["count"]
        )

    def _count_memories(self, processing_run_uuid: str) -> int:
        row = self.connection.execute(
            "SELECT COUNT(DISTINCT mel.memory_uuid) AS count FROM memory_evidence_links mel JOIN memory_candidates mc ON mc.candidate_uuid = mel.candidate_uuid WHERE mc.processing_run_uuid = %s",
            (processing_run_uuid,),
        ).fetchone()
        return int(row["count"])


def _scope_for_message(message: dict[str, Any]) -> tuple[str, str]:
    if message.get("project_uuid"):
        return "project", str(message["project_uuid"])
    if message.get("workspace_uuid"):
        return "workspace", str(message["workspace_uuid"])
    return "session", str(message["session_uuid"])


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _json_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _active_contract_hash() -> str:
    contract = get_contract(
        JAKOBSON_SENTENCE_ANALYSIS_PROMPT_ID,
        JAKOBSON_SENTENCE_ANALYSIS_ACTIVE_VERSION,
    )
    if contract is None:
        raise RuntimeError("active Jakobson contract is not registered")
    return contract.contract_hash


def _capability_mode(profile: dict[str, Any]) -> str:
    if bool(profile.get("supports_structured_output")):
        return "json_schema"
    if bool(profile.get("supports_json_mode")):
        return "json_object"
    return "incompatible"
