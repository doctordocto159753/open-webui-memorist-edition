from __future__ import annotations

import hashlib
import unicodedata
import uuid
from collections.abc import Mapping
from typing import Any

from memcore.memory_worker.execution import ContractExecutionOutcome
from memcore.memory_worker.prompts.contracts import SemanticAnalysisV1Output
from memcore.models import utc_now
from memcore.validators.ijson import dump_ijson

_ANALYSIS_NAMESPACE = uuid.UUID("7018f153-3202-5b3a-8e13-98248e397d61")
_UNIT_NAMESPACE = uuid.UUID("38dab83c-00cb-58ba-a389-b04ab60eb816")
_CONCEPT_NAMESPACE = uuid.UUID("091a2934-eb66-5a40-b915-0ac7f0375489")
_REFERENCE_NAMESPACE = uuid.UUID("b0a099a9-4baa-50d2-ae4e-73a188c12db9")


def persist_message_semantics(
    connection: Any,
    *,
    postgres: bool,
    message_uuid: str,
    processing_run_uuid: str,
    prompt_execution_uuid: str,
    stage_execution_uuid: str,
    contract_hash: str,
    scope: Any,
    input_payload: Mapping[str, Any],
    outcome: ContractExecutionOutcome,
) -> str:
    """Persist normalized model proposals without granting direct write authority."""

    output = SemanticAnalysisV1Output.model_validate(outcome.output)
    raw_text = str(input_payload["current_raw_text"])
    raw_hash = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()
    identity = f"{message_uuid}:{processing_run_uuid}:{contract_hash}:{raw_hash}"
    analysis_uuid = str(uuid.uuid5(_ANALYSIS_NAMESPACE, identity))
    placeholder = "%s" if postgres else "?"
    existing = connection.execute(
        f"SELECT 1 FROM message_semantic_analyses WHERE semantic_analysis_uuid = {placeholder}",
        (analysis_uuid,),
    ).fetchone()
    if existing is not None:
        return analysis_uuid

    now = utc_now()
    role = str(scope.role)
    authority = {
        "user": "user_explicit",
        "assistant": "assistant_claim",
        "tool": "tool_observation",
        "system": "system_instruction",
    }.get(role, "untrusted_record")
    status = _analysis_status(output, outcome)
    semantic_outcome = _semantic_outcome(output, outcome)
    warnings_value = dump_ijson(output.warnings)
    analysis_values = (
        analysis_uuid,
        message_uuid,
        scope.message_version_uuid,
        processing_run_uuid,
        prompt_execution_uuid,
        stage_execution_uuid,
        scope.workspace_uuid,
        scope.project_uuid,
        scope.session_uuid,
        scope.user_uuid,
        role,
        authority,
        contract_hash,
        raw_hash,
        status,
        semantic_outcome,
        output.intent,
        output.primary_topic,
        output.secondary_topic,
        output.one_line_summary,
        output.epistemic_status,
        output.temporal_status,
        output.importance,
        output.explicit_memory_request,
        warnings_value,
        now,
        now,
    )
    warnings_cast = f"{placeholder}::jsonb" if postgres else placeholder
    connection.execute(
        f"""
        INSERT INTO message_semantic_analyses (
          semantic_analysis_uuid, message_uuid, message_version_uuid,
          processing_run_uuid, prompt_execution_uuid, stage_execution_uuid,
          workspace_uuid, project_uuid, session_uuid, user_uuid, source_role,
          source_authority, contract_hash, raw_text_hash, status, semantic_outcome,
          summary_intent, primary_topic, secondary_topic, one_line_summary,
          epistemic_status, temporal_status, importance, explicit_memory_request,
          {'warnings_jsonb' if postgres else 'warnings_ijson'}, created_at, updated_at
        ) VALUES ({', '.join([placeholder] * 24)}, {warnings_cast}, {placeholder}, {placeholder})
        """,
        analysis_values,
    )
    for category in output.message_categories:
        connection.execute(
            f"INSERT INTO message_semantic_categories "
            f"(semantic_analysis_uuid, category, normalized_label, confidence) "
            f"VALUES ({placeholder}, {placeholder}, {placeholder}, {placeholder})",
            (analysis_uuid, category.category, category.normalized_label, category.confidence),
        )
    for ordinal, tag in enumerate(output.concept_tags):
        normalized = _normalize_label(tag.canonical_label)
        concept_uuid = str(uuid.uuid5(_CONCEPT_NAMESPACE, normalized))
        connection.execute(
            f"INSERT INTO canonical_concepts (concept_uuid, canonical_label, created_at) "
            f"VALUES ({placeholder}, {placeholder}, {placeholder}) "
            f"ON CONFLICT (concept_uuid) DO NOTHING",
            (concept_uuid, normalized, now),
        )
        for alias in dict.fromkeys([tag.canonical_label, *tag.aliases]):
            normalized_alias = _normalize_label(alias)
            connection.execute(
                f"INSERT INTO concept_aliases "
                f"(concept_uuid, alias, normalized_alias, language) "
                f"VALUES ({placeholder}, {placeholder}, {placeholder}, {placeholder}) "
                f"ON CONFLICT (normalized_alias) DO NOTHING",
                (concept_uuid, alias, normalized_alias, None),
            )
        connection.execute(
            f"INSERT INTO message_concept_tags "
            f"(semantic_analysis_uuid, concept_uuid, tag_ordinal, confidence, raw_start, raw_end) "
            f"VALUES ({', '.join([placeholder] * 6)})",
            (analysis_uuid, concept_uuid, ordinal, tag.confidence, tag.raw_start, tag.raw_end),
        )
    for ordinal, unit in enumerate(output.semantic_units):
        unit_uuid = str(uuid.uuid5(_UNIT_NAMESPACE, f"{analysis_uuid}:{unit.id}"))
        evidence_hash = hashlib.sha256(unit.evidence.encode("utf-8")).hexdigest()
        connection.execute(
            f"""
            INSERT INTO message_semantic_units (
              semantic_unit_uuid, semantic_analysis_uuid, semantic_unit_id, unit_ordinal,
              raw_start, raw_end, evidence_hash, proposition_text, unit_type, memory_kind,
              durability, polarity, epistemic_status, lifecycle_status, created_at
            ) VALUES ({', '.join([placeholder] * 15)})
            """,
            (
                unit_uuid,
                analysis_uuid,
                unit.id,
                ordinal,
                unit.raw_start,
                unit.raw_end,
                evidence_hash,
                unit.proposition,
                unit.unit_type,
                unit.memory_kind,
                unit.durability,
                unit.polarity,
                unit.epistemic_status,
                unit.lifecycle_status,
                now,
            ),
        )
    _persist_references(connection, postgres, placeholder, analysis_uuid, output, now)
    if postgres:
        connection.execute(
            """
            INSERT INTO graph_projection_outbox (
              outbox_uuid, event_type, source_type, source_uuid, payload_jsonb,
              status, priority, created_at, updated_at
            ) VALUES (%s, 'message_semantics_upserted', 'message_semantics', %s,
                      %s::jsonb, 'pending', 70, %s, %s)
            ON CONFLICT (event_type, source_type, source_uuid) DO UPDATE
            SET status = 'pending', payload_jsonb = EXCLUDED.payload_jsonb,
                updated_at = EXCLUDED.updated_at
            """,
            (
                str(uuid.uuid5(_REFERENCE_NAMESPACE, f"outbox:{analysis_uuid}")),
                analysis_uuid,
                dump_ijson({"semantic_analysis_uuid": analysis_uuid}),
                now,
                now,
            ),
        )
    job_outcome_uuid = str(uuid.uuid5(_REFERENCE_NAMESPACE, f"outcome:{analysis_uuid}"))
    connection.execute(
        f"""
        INSERT INTO semantic_job_outcomes (
          semantic_job_outcome_uuid, semantic_analysis_uuid, message_uuid,
          processing_run_uuid, job_uuid, outcome, reason_code, called_provider,
          provider_output_valid, fallback_used, candidate_count, memory_count,
          latency_ms, created_at
        ) VALUES ({', '.join([placeholder] * 14)})
        """,
        (
            job_outcome_uuid,
            analysis_uuid,
            message_uuid,
            processing_run_uuid,
            None,
            semantic_outcome,
            outcome.fallback_reason or output.status,
            outcome.called_provider,
            outcome.provider_output_valid,
            outcome.fallback_used,
            0,
            0,
            outcome.latency_ms,
            now,
        ),
    )
    return analysis_uuid


def update_semantic_job_outcome(
    connection: Any,
    *,
    postgres: bool,
    processing_run_uuid: str,
    candidate_count: int,
    memory_count: int,
    partial: bool,
) -> str:
    placeholder = "%s" if postgres else "?"
    if memory_count:
        outcome = "succeeded_with_memory"
    elif candidate_count:
        outcome = "succeeded_with_candidates_only"
    elif partial:
        outcome = "succeeded_with_partial_semantics"
    else:
        outcome = "succeeded_no_candidate"
    connection.execute(
        f"""
        UPDATE semantic_job_outcomes
        SET outcome = {placeholder}, candidate_count = {placeholder}, memory_count = {placeholder}
        WHERE processing_run_uuid = {placeholder}
        """,
        (outcome, candidate_count, memory_count, processing_run_uuid),
    )
    return outcome


def _persist_references(
    connection: Any,
    postgres: bool,
    placeholder: str,
    analysis_uuid: str,
    output: SemanticAnalysisV1Output,
    now: str,
) -> None:
    aliases_column = "aliases_jsonb" if postgres else "aliases_ijson"
    process_aliases_column = "process_aliases_jsonb" if postgres else "process_aliases_ijson"
    for index, entity in enumerate(output.entities):
        reference_uuid = str(uuid.uuid5(_REFERENCE_NAMESPACE, f"entity:{analysis_uuid}:{index}"))
        aliases = dump_ijson(entity.aliases)
        alias_value = f"{placeholder}::jsonb" if postgres else placeholder
        connection.execute(
            f"INSERT INTO message_entity_references "
            f"(entity_reference_uuid, semantic_analysis_uuid, canonical_name, entity_type, "
            f"{aliases_column}, raw_start, raw_end, confidence) "
            f"VALUES ({placeholder}, {placeholder}, {placeholder}, {placeholder}, {alias_value}, "
            f"{placeholder}, {placeholder}, {placeholder})",
            (
                reference_uuid,
                analysis_uuid,
                entity.canonical_name,
                entity.entity_type,
                aliases,
                entity.raw_start,
                entity.raw_end,
                entity.confidence,
            ),
        )
    for index, process in enumerate(output.process_references):
        reference_uuid = str(uuid.uuid5(_REFERENCE_NAMESPACE, f"process:{analysis_uuid}:{index}"))
        aliases = dump_ijson(process.aliases)
        alias_value = f"{placeholder}::jsonb" if postgres else placeholder
        connection.execute(
            f"INSERT INTO message_process_references "
            f"(process_reference_uuid, semantic_analysis_uuid, process_label, "
            f"{process_aliases_column}, stage_label, stage_ordinal, raw_start, raw_end, "
            f"confidence) "
            f"VALUES ({placeholder}, {placeholder}, {placeholder}, {alias_value}, "
            f"{placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder})",
            (
                reference_uuid,
                analysis_uuid,
                process.process_label,
                aliases,
                process.stage_label,
                process.stage_ordinal,
                process.raw_start,
                process.raw_end,
                process.confidence,
            ),
        )
    del now


def _normalize_label(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _analysis_status(
    output: SemanticAnalysisV1Output, outcome: ContractExecutionOutcome
) -> str:
    if outcome.fallback_used:
        return "failed_open"
    return "succeeded" if output.status == "ok" else "abstained"


def _semantic_outcome(
    output: SemanticAnalysisV1Output, outcome: ContractExecutionOutcome
) -> str:
    if outcome.fallback_used:
        return "succeeded_with_failed_open_stage"
    if output.status == "abstain":
        return "succeeded_with_abstention"
    return "succeeded_with_partial_semantics" if output.warnings else "succeeded_no_candidate"
