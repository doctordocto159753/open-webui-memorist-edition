from __future__ import annotations

# ruff: noqa: E501
import json
from hashlib import sha256
from time import perf_counter
from types import SimpleNamespace
from typing import Any

from fastapi import HTTPException

from memcore.config import Settings
from memcore.memory_worker.contracts import PIPELINE_VERSION, PROMPT_BUNDLE_VERSION
from memcore.memory_worker.gating import DeterministicGate
from memcore.memory_worker.identity import build_processing_identity
from memcore.memory_worker.prompts import render_prompt, validate_prompt_execution
from memcore.memory_worker.prompts.versions import (
    JAKOBSON_SENTENCE_ANALYSIS_PROMPT_ID,
    PROMPT_PACK_VERSION,
)
from memcore.memory_worker.providers.openai_compatible import (
    OpenAICompatibleMemoryExtractionProvider,
)
from memcore.memory_worker.segmentation.sentence_segmenter import SentenceSegmenter
from memcore.model_control.security import sanitize_error_message
from memcore.models import new_uuid, utc_now
from memcore.validators.ijson import canonical_hash_ijson


class PostgresMemoryWorkerPipeline:
    def __init__(self, connection: Any, settings: Settings) -> None:
        self.connection = connection
        self.settings = settings
        self.segmenter = SentenceSegmenter()
        self.gate = DeterministicGate()

    def process_message(self, message_uuid: str, import_run_uuid: str | None = None, job_uuid: str | None = None, model_target: dict[str, Any] | None = None) -> dict[str, object]:
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
        profile = self._resolve_profile()
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
        )
        content_hash = identity.input_content_hash
        run = self._get_or_create_run(message, identity, model_profile_uuid, provider_type)
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
            if profile and provider_type in {"openai_compatible", "openai_compatible_llm"}:
                output, prompt_execution_uuid, usage = self._run_model_prompt(
                    profile, input_payload, message, model_name, provider_type
                )
            else:
                output = self._deterministic_jakobson_output(units)
                prompt_execution_uuid, usage = (
                    self._record_prompt_execution(
                        input_payload=input_payload,
                        output=output,
                        message=message,
                        model_profile_uuid=model_profile_uuid,
                        provider_type="deterministic",
                        model_name=model_name,
                        latency_ms=0,
                        input_tokens=0,
                        output_tokens=0,
                    ),
                    {"input_tokens": 0, "output_tokens": 0, "latency_ms": 0},
                )
            self._record_usage(message, model_profile_uuid, provider_type, model_name, usage)
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
            )
            memories = self._record_memories(
                message, candidates, content_hash, prompt_execution_uuid
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
            }
        except Exception as error:
            self.connection.execute(
                "UPDATE memory_processing_runs SET status = 'failed', finished_at = %s, error_sanitized = %s WHERE processing_run_uuid = %s",
                (utc_now(), sanitize_error_message(str(error)), run["processing_run_uuid"]),
            )
            raise

    def _resolve_profile(self) -> dict[str, Any] | None:
        row = self.connection.execute(
            """
            SELECT p.* FROM model_role_defaults d JOIN model_profiles p ON p.model_profile_uuid = d.model_profile_uuid
            WHERE d.role = 'memory_extraction' AND COALESCE(p.is_enabled, true) = true
            ORDER BY d.created_at DESC LIMIT 1
            """
        ).fetchone()
        if row is None:
            return None
        profile = dict(row)
        if profile.get("requires_privacy_acknowledgement") and not profile.get(
            "privacy_acknowledged_at"
        ):
            raise HTTPException(
                status_code=400, detail="memory extraction profile requires privacy acknowledgement"
            )
        return profile

    def _get_or_create_run(
        self,
        message: dict[str, Any],
        identity: Any,
        model_profile_uuid: str | None,
        provider_type: str,
    ) -> dict[str, Any]:
        existing = self.connection.execute(
            """
            SELECT * FROM memory_processing_runs
            WHERE message_uuid = %s AND pipeline_version = %s AND prompt_bundle_version = %s
              AND input_content_hash = %s AND COALESCE(model_profile_uuid, '') = COALESCE(%s, '')
              AND COALESCE(provider_type, '') = COALESCE(%s, '')
              AND COALESCE(prompt_id, '') = COALESCE(%s, '')
              AND COALESCE(prompt_version, '') = COALESCE(%s, '')
            ORDER BY created_at LIMIT 1
            """,
            (
                message["message_uuid"],
                identity.pipeline_version,
                identity.prompt_bundle_version,
                identity.input_content_hash,
                model_profile_uuid,
                provider_type,
                identity.prompt_id,
                identity.prompt_version,
            ),
        ).fetchone()
        if existing is not None:
            return dict(existing)
        run_uuid = new_uuid()
        self.connection.execute(
            """
            INSERT INTO memory_processing_runs (processing_run_uuid, session_uuid, message_uuid, pipeline_version, prompt_bundle_version,
              input_content_hash, status, created_at, schema_version, model_profile_uuid, provider_type, input_hash)
            VALUES (%s, %s, %s, %s, %s, %s, 'pending', %s, 1, %s, %s, %s)
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
        prompt_execution_uuid = self._record_prompt_execution(
            input_payload=input_payload,
            output=response.output,
            message=message,
            model_profile_uuid=str(profile["model_profile_uuid"]),
            provider_type=provider_type,
            model_name=model_name,
            latency_ms=response.latency_ms,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
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
        latency_ms: int,
        input_tokens: int,
        output_tokens: int,
    ) -> str:
        validate_prompt_execution(
            JAKOBSON_SENTENCE_ANALYSIS_PROMPT_ID, PROMPT_PACK_VERSION, input_payload, output
        )
        prompt_execution_uuid = new_uuid()
        self.connection.execute(
            """
            INSERT INTO prompt_execution_runs (prompt_execution_uuid, prompt_id, prompt_version, stage, model_profile_uuid, model_role, provider_type,
              model_name, workspace_uuid, project_uuid, session_uuid, message_uuid, input_hash, output_hash, raw_output_ijson, validated_output_ijson,
              status, warnings_ijson, error_sanitized, latency_ms, input_tokens, output_tokens, created_at, schema_version)
            VALUES (%s,%s,%s,'jakobson_sentence_analysis',%s,'memory_extraction',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'ok',%s,NULL,%s,%s,%s,%s,1)
            """,
            (
                prompt_execution_uuid,
                JAKOBSON_SENTENCE_ANALYSIS_PROMPT_ID,
                PROMPT_PACK_VERSION,
                model_profile_uuid,
                provider_type,
                model_name,
                message.get("workspace_uuid"),
                message.get("project_uuid"),
                message["session_uuid"],
                message["message_uuid"],
                canonical_hash_ijson(input_payload),
                canonical_hash_ijson(output),
                json.dumps(output, sort_keys=True),
                json.dumps(output, sort_keys=True),
                json.dumps(output.get("warnings", []), sort_keys=True),
                latency_ms,
                input_tokens,
                output_tokens,
                utc_now(),
            ),
        )
        return prompt_execution_uuid

    def _record_usage(
        self,
        message: dict[str, Any],
        model_profile_uuid: str | None,
        provider_type: str,
        model_name: str,
        usage: dict[str, int],
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO model_usage_events (usage_event_uuid, model_profile_uuid, role, event_type, input_tokens, output_tokens, created_at, schema_version,
              stage, provider_type, model_name, workspace_uuid, project_uuid, session_uuid, message_uuid, latency_ms, status)
            VALUES (%s,%s,'memory_extraction','prompt_execution',%s,%s,%s,1,'jakobson_sentence_analysis',%s,%s,%s,%s,%s,%s,%s,'ok')
            """,
            (
                new_uuid(),
                model_profile_uuid,
                int(usage.get("input_tokens", 0)),
                int(usage.get("output_tokens", 0)),
                utc_now(),
                provider_type,
                model_name,
                message.get("workspace_uuid"),
                message.get("project_uuid"),
                message["session_uuid"],
                message["message_uuid"],
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
        extractor_by_function = {
            "conative": "memorist.conative_instruction_extractor",
            "referential": "memorist.referential_context_extractor",
            "metalingual": "memorist.metalingual_policy_extractor",
            "emotive": "memorist.emotive_preference_extractor",
            "poetic": "memorist.poetic_style_extractor",
            "phatic": "memorist.referential_context_extractor",
        }
        type_by_function = {
            "conative": "task_constraint",
            "referential": "process_fact",
            "metalingual": "terminology_rule",
            "emotive": "user_preference",
            "poetic": "style_policy",
            "phatic": "process_fact",
        }
        for annotation in annotations:
            func = str(annotation["dominant_function"])
            route_uuid = new_uuid()
            self.connection.execute(
                """
                INSERT INTO memory_signal_routes (route_uuid, annotation_uuid, message_uuid, unit_uuid, dominant_function, secondary_functions_jsonb, route_type,
                  extractor_id, priority, confidence, reason, status, created_at, schema_version)
                VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s,%s,50,'0.75','structured extraction route','ready',%s,1)
                ON CONFLICT (annotation_uuid, route_type, extractor_id) DO NOTHING
                """,
                (
                    route_uuid,
                    annotation["annotation_uuid"],
                    message_uuid,
                    annotation["unit_uuid"],
                    func,
                    json.dumps(annotation.get("secondary_functions_jsonb") or []),
                    type_by_function.get(func, "process_fact"),
                    extractor_by_function.get(func, "memorist.referential_context_extractor"),
                    utc_now(),
                ),
            )
        return [
            dict(row)
            for row in self.connection.execute(
                "SELECT * FROM memory_signal_routes WHERE message_uuid = %s", (message_uuid,)
            ).fetchall()
        ]

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
                SimpleNamespace(text=unit["text"], speaker_role=unit["speaker_role"])
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
    ) -> list[dict[str, Any]]:
        route_by_unit = {r["unit_uuid"]: r for r in routes}
        annotation_by_unit = {a["unit_uuid"]: a for a in annotations}
        for unit in units:
            existing = self.connection.execute(
                "SELECT 1 FROM memory_candidates WHERE processing_run_uuid = %s AND text_unit_uuid = %s AND normalized_text = %s",
                (processing_run_uuid, unit["text_unit_uuid"], unit["text"]),
            ).fetchone()
            if existing:
                continue
            route = route_by_unit.get(unit["text_unit_uuid"])
            annotation = annotation_by_unit.get(unit["text_unit_uuid"])
            candidate_uuid = new_uuid()
            self.connection.execute(
                """
                INSERT INTO memory_candidates (candidate_uuid, processing_run_uuid, text_unit_uuid, candidate_type, subject_key, predicate, object_jsonb,
                  normalized_text, source_authority, explicitness, confidence, importance, sensitivity, status, rejection_reason, extraction_metadata_jsonb,
                  created_at, schema_version, prompt_execution_uuid)
                VALUES (%s,%s,%s,'observation',%s,'states',%s::jsonb,%s,'user_message','explicit',0.76,0.55,'normal','accepted',NULL,%s::jsonb,%s,1,%s)
                """,
                (
                    candidate_uuid,
                    processing_run_uuid,
                    unit["text_unit_uuid"],
                    f"message:{message['message_uuid']}",
                    json.dumps({"text": unit["text"]}),
                    unit["text"],
                    json.dumps(
                        {
                            "provider_type": provider_type,
                            "prompt_id": JAKOBSON_SENTENCE_ANALYSIS_PROMPT_ID,
                        }
                    ),
                    utc_now(),
                    prompt_execution_uuid,
                ),
            )
            self.connection.execute(
                """
                INSERT INTO candidate_evidence (evidence_uuid, candidate_uuid, message_uuid, text_unit_uuid, annotation_uuid, route_uuid, evidence_text, start_char, end_char, created_at, schema_version)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,1)
                """,
                (
                    new_uuid(),
                    candidate_uuid,
                    message["message_uuid"],
                    unit["text_unit_uuid"],
                    annotation["annotation_uuid"] if annotation else None,
                    route["route_uuid"] if route else None,
                    unit["text"],
                    unit["start_char"],
                    unit["end_char"],
                    utc_now(),
                ),
            )
        return [
            dict(row)
            for row in self.connection.execute(
                "SELECT * FROM memory_candidates WHERE processing_run_uuid = %s",
                (processing_run_uuid,),
            ).fetchall()
        ]

    def _record_memories(
        self,
        message: dict[str, Any],
        candidates: list[dict[str, Any]],
        content_hash: str,
        prompt_execution_uuid: str,
    ) -> int:
        created = 0
        for candidate in candidates:
            memory_uuid = new_uuid()
            version_uuid = new_uuid()
            canonical_key = (
                f"message:{message['message_uuid']}:candidate:{candidate['candidate_uuid']}"
            )
            existing = self.connection.execute(
                "SELECT memory_uuid FROM memories WHERE scope_type = 'session' AND scope_uuid = %s AND canonical_key = %s",
                (message["session_uuid"], canonical_key),
            ).fetchone()
            if existing:
                continue
            self.connection.execute(
                "INSERT INTO memories (memory_uuid, scope_type, scope_uuid, canonical_key, current_version_uuid, status, created_at, updated_at, schema_version) VALUES (%s,'session',%s,%s,%s,'active',%s,%s,1)",
                (
                    memory_uuid,
                    message["session_uuid"],
                    canonical_key,
                    version_uuid,
                    utc_now(),
                    utc_now(),
                ),
            )
            self.connection.execute(
                "INSERT INTO memory_versions (memory_version_uuid, memory_uuid, version_number, operation, value, normalized_text, confidence, importance, source_snapshot_hash, transaction_from, valid_from, status, created_at, schema_version, prompt_execution_uuid) VALUES (%s,%s,1,'create',%s,%s,%s,%s,%s,%s,%s,'current',%s,1,%s)",
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
        sentences = []
        for idx, unit in enumerate(units, start=1):
            text = unit["text"]
            sentences.append(
                {
                    "id": idx,
                    "text": text,
                    "six_factors": {
                        "sender_addresser": {
                            "value": "user",
                            "evidence": text,
                            "confidence": "medium",
                        },
                        "receiver_addressee": {
                            "value": "assistant",
                            "evidence": text,
                            "confidence": "medium",
                        },
                        "message": {"value": text, "evidence": text, "confidence": "high"},
                        "context_referent": {
                            "value": text,
                            "evidence": text,
                            "confidence": "medium",
                        },
                        "code": {
                            "value": "natural language",
                            "evidence": text,
                            "confidence": "medium",
                        },
                        "contact_channel": {
                            "value": "chat",
                            "evidence": text,
                            "confidence": "medium",
                        },
                    },
                    "dominant_function": "referential",
                    "secondary_functions": [],
                    "function_reason": "Deterministic fallback classifies the sentence as referential.",
                    "notes": "deterministic fallback",
                }
            )
        return {
            "schema_version": "1.0",
            "prompt_id": JAKOBSON_SENTENCE_ANALYSIS_PROMPT_ID,
            "prompt_version": PROMPT_PACK_VERSION,
            "status": "ok",
            "warnings": ["deterministic_fallback"],
            "items": [],
            "analysis_level": "sentence",
            "model": "jakobson_six_factor",
            "input_language": "mixed",
            "sentence_count": len(sentences),
            "sentences": sentences,
            "overall_summary": {
                "dominant_overall_function": "referential",
                "secondary_overall_functions": [],
                "main_sender": "user",
                "main_receiver": "assistant",
                "main_context": "conversation",
                "main_code": "natural language",
                "main_contact_channel": "chat",
            },
        }

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
