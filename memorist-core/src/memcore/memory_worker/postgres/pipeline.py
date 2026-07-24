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
from memcore.memory_worker.contracts import PIPELINE_VERSION, PROMPT_BUNDLE_VERSION
from memcore.memory_worker.gating import DeterministicGate
from memcore.memory_worker.identity import build_processing_identity
from memcore.memory_worker.postgres.deterministic_fallback import (
    deterministic_jakobson_output,
)
from memcore.memory_worker.postgres.gated_candidate_adapter import (
    record_candidates,
)
from memcore.memory_worker.postgres.routing_policy_adapter import record_routes
from memcore.memory_worker.prepared import PreparedJakobsonInference
from memcore.memory_worker.prompts import render_prompt, validate_prompt_execution
from memcore.memory_worker.prompts.versions import (
    JAKOBSON_SENTENCE_ANALYSIS_PROMPT_ID,
    PROMPT_PACK_VERSION,
)
from memcore.memory_worker.providers.openai_compatible import (
    OpenAICompatibleMemoryExtractionProvider,
)
from memcore.memory_worker.segmentation.sentence_segmenter import SentenceSegmenter
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
from memcore.models import ModelRole, new_uuid, utc_now
from memcore.validators.ijson import canonical_hash_ijson


class PostgresMemoryWorkerPipeline:
    def __init__(self, connection: Any, settings: Settings) -> None:
        self.connection = connection
        self.settings = settings
        self.segmenter = SentenceSegmenter()
        self.gate = DeterministicGate()

    def prepare_message(
        self,
        message_uuid: str,
        model_target: dict[str, Any] | None = None,
    ) -> PreparedJakobsonInference:
        """Run and validate provider inference before opening a write transaction."""
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
        # psycopg starts a transaction even for SELECT. End that read transaction
        # before the potentially slow HTTP request.
        self.connection.commit()
        if provider_type in {"openai_compatible", "openai_compatible_llm"}:
            prompt = render_prompt(
                JAKOBSON_SENTENCE_ANALYSIS_PROMPT_ID, PROMPT_PACK_VERSION, input_payload
            )
            response = OpenAICompatibleMemoryExtractionProvider.from_profile(profile).extract(
                system_prompt=prompt, input_payload=input_payload
            )
            validate_prompt_execution(
                JAKOBSON_SENTENCE_ANALYSIS_PROMPT_ID,
                PROMPT_PACK_VERSION,
                input_payload,
                response.output,
            )
            output = response.output
            input_tokens = response.input_tokens
            output_tokens = response.output_tokens
            latency_ms = response.latency_ms
        else:
            output = self._deterministic_jakobson_output(
                [
                    {
                        "text_unit_uuid": unit.text_unit_uuid,
                        "text": unit.text,
                        "start_char": unit.start_char,
                        "end_char": unit.end_char,
                    }
                    for unit in units
                ]
            )
            validate_prompt_execution(
                JAKOBSON_SENTENCE_ANALYSIS_PROMPT_ID,
                PROMPT_PACK_VERSION,
                input_payload,
                output,
            )
            input_tokens = max(0, (len(raw_text) + 3) // 4)
            output_tokens = len(output.get("sentences", []))
            latency_ms = 0
        return PreparedJakobsonInference(
            message_uuid=message_uuid,
            model_role=model_role,
            model_profile_uuid=model_profile_uuid,
            provider_type=provider_type,
            model_name=model_name,
            processing_identity_hash=identity.identity_hash,
            input_content_hash=identity.input_content_hash,
            output=output,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
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
        ):
            raise RuntimeError("prepared inference processing identity mismatch")
        content_hash = identity.input_content_hash
        run = self._get_or_create_run(
            message, identity, model_profile_uuid, provider_type, model_name
        )
        if run["status"] == "succeeded":
            return self._summary(message_uuid, str(run["processing_run_uuid"]), True)
        self.connection.execute(
            "UPDATE memory_processing_runs SET status = 'running', started_at = COALESCE(started_at, %s) WHERE processing_run_uuid = %s",
            (utc_now(), run["processing_run_uuid"]),
        )
        try:
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
            if prepared_inference is not None:
                self._validate_prepared_inference(
                    prepared_inference,
                    message_uuid=message_uuid,
                    model_role=model_role,
                    model_profile_uuid=model_profile_uuid,
                    provider_type=provider_type,
                    model_name=model_name,
                )
                output = prepared_inference.output
                validate_prompt_execution(
                    JAKOBSON_SENTENCE_ANALYSIS_PROMPT_ID,
                    PROMPT_PACK_VERSION,
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
                )
                usage = {
                    "input_tokens": prepared_inference.input_tokens,
                    "output_tokens": prepared_inference.output_tokens,
                    "latency_ms": prepared_inference.latency_ms,
                }
            elif profile and provider_type in {"openai_compatible", "openai_compatible_llm"}:
                output, prompt_execution_uuid, usage = self._run_model_prompt(
                    profile,
                    input_payload,
                    message,
                    model_name,
                    provider_type,
                    model_role,
                    import_run_uuid,
                    job_uuid,
                    lease_fence,
                )
            else:
                output = self._deterministic_jakobson_output(units)
                if lease_fence is not None:
                    lease_fence()
                prompt_execution_uuid, usage = (
                    self._record_prompt_execution(
                        input_payload=input_payload,
                        output=output,
                        message=message,
                        model_profile_uuid=model_profile_uuid,
                        provider_type="deterministic",
                        model_name=model_name,
                        model_role=model_role,
                        latency_ms=0,
                        input_tokens=0,
                        output_tokens=0,
                        import_run_uuid=import_run_uuid,
                        job_uuid=job_uuid,
                    ),
                    {"input_tokens": 0, "output_tokens": 0, "latency_ms": 0},
                )
            self._record_usage(
                message,
                model_profile_uuid,
                provider_type,
                model_name,
                model_role,
                import_run_uuid,
                job_uuid,
                usage,
            )
            analysis_run_uuid = self._record_jakobson(
                message,
                input_payload,
                output,
                model_profile_uuid,
                provider_type,
                model_name,
                prompt_execution_uuid,
            )
            annotations = self._record_annotations(message_uuid, analysis_run_uuid, units, output)
            routes = self._record_routes(message_uuid, annotations)
            decisions = self._record_gates(str(run["processing_run_uuid"]), units)
            analyses = self._record_linguistic_analyses(
                str(run["processing_run_uuid"]), units, output
            )
            candidates = self._record_candidates(
                str(run["processing_run_uuid"]),
                message,
                units,
                annotations,
                routes,
                prompt_execution_uuid,
                provider_type,
                import_run_uuid,
                model_name,
                analyses=analyses,
            )
            candidate_stage_results = self._run_candidate_stages(
                str(run["processing_run_uuid"]),
                message,
                candidates,
            )
            memories = self._record_memories(
                message, candidates, content_hash, prompt_execution_uuid
            )
            embedding_results = self._run_embedding_stages(
                str(run["processing_run_uuid"]),
                message,
                candidates,
            )
            self.connection.execute(
                "UPDATE messages SET processing_status = 'available', updated_at = %s WHERE message_uuid = %s",
                (utc_now(), message_uuid),
            )
            self.connection.execute(
                "UPDATE memory_processing_runs SET status = 'succeeded', finished_at = %s WHERE processing_run_uuid = %s",
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
                "latency_ms": int((perf_counter() - started) * 1000),
                "memories_created": memories,
                "candidate_stage_results": candidate_stage_results,
                "embedding_results": embedding_results,
            }
        except Exception as error:
            self.connection.execute(
                "UPDATE memory_processing_runs SET status = 'failed', finished_at = %s, error_sanitized = %s WHERE processing_run_uuid = %s",
                (utc_now(), sanitize_error_message(str(error)), run["processing_run_uuid"]),
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
            project_uuid=(
                str(message["project_uuid"]) if message.get("project_uuid") else None
            ),
        )
        if resolution.model_profile_uuid:
            profile = repository.get_profile(resolution.model_profile_uuid)
            if profile is not None:
                values = profile.model_dump(mode="json")
                values["model_role"] = ModelRole.MEMORY_EXTRACTION.value
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
        provider = OpenAICompatibleMemoryExtractionProvider.from_profile(profile, timeout_ms=8000)
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
    ) -> str:
        validate_prompt_execution(
            JAKOBSON_SENTENCE_ANALYSIS_PROMPT_ID, PROMPT_PACK_VERSION, input_payload, output
        )
        prompt_execution_uuid = new_uuid()
        row = {
            "prompt_execution_uuid": prompt_execution_uuid,
            "prompt_id": JAKOBSON_SENTENCE_ANALYSIS_PROMPT_ID,
            "prompt_version": PROMPT_PACK_VERSION,
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
              %s,%s,%s,%s,%s,%s,%s,%s,%s,'ok'
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
                PROMPT_PACK_VERSION,
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
        for idx, sentence in enumerate(output.get("sentences", [])):
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
                    idx,
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
        sentences = output.get("sentences", [])
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
                    json.dumps({}),
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

    def _run_candidate_stages(
        self,
        processing_run_uuid: str,
        message: dict[str, Any],
        candidates: list[dict[str, Any]],
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
            )
            self.connection.commit()
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
            )
            self.connection.commit()
            decision = str((high.output or {}).get("decision") or "abstain")
            if decision == "rejected":
                status = "rejected"
            elif decision in {"needs_review", "abstain"} and status != "rejected":
                status = "needs_review"
            metadata["high_confidence_stage_status"] = decision
            metadata["high_confidence_execution_uuid"] = high.execution_uuid
            self.connection.execute(
                "UPDATE memory_candidates SET status = %s, extraction_metadata_jsonb = %s::jsonb "
                "WHERE candidate_uuid = %s",
                (status, json.dumps(metadata, sort_keys=True), candidate["candidate_uuid"]),
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
    ) -> list[dict[str, Any]]:
        repository = PostgresModelControlRepository(self.connection)
        invoker = StageInvoker(self.connection, repository, postgres=True)
        resolution = invoker.resolver.resolve(
            ModelRole.EMBEDDING,
            workspace_uuid=_optional_text(message.get("workspace_uuid")),
            project_uuid=_optional_text(message.get("project_uuid")),
        )
        if resolution.provider_type == "disabled":
            return []
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
            self.connection.commit()
            # A replayed stage returns no vectors, so only accept a replay when
            # the projection row from the first execution actually exists.
            projection_exists = (
                self.connection.execute(
                    "SELECT 1 FROM memory_version_embeddings "
                    "WHERE memory_version_uuid = %s AND content_hash = %s",
                    (version_uuid, content_hash),
                ).fetchone()
                is not None
            )
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
                    input_payload={"text": text, "content_hash": content_hash},
                ),
                texts=[text],
                expected_dimension=expected_dimension,
                allow_replay=projection_exists,
            )
            self.connection.commit()
            if vectors and result.model_profile_uuid:
                vector = vectors[0]
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
                "INSERT INTO memory_versions (memory_version_uuid, memory_uuid, version_number, operation, value, normalized_text, confidence, importance, source_snapshot_hash, transaction_from, valid_from, status, created_at, schema_version, prompt_execution_uuid, source_candidate_uuid) VALUES (%s,%s,1,'create',%s,%s,%s,%s,%s,%s,%s,'current',%s,1,%s,%s)",
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
