from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from memcore.memory_worker.prompts.registry import (
    PROMPT_PACK_ID,
    PromptExecutionRepository,
    PromptInvocationRepository,
    PromptValidationError,
    get_prompt,
    list_prompts,
    render_prompt,
    validate_prompt_execution,
    validate_prompt_input,
    validate_prompt_output,
)
from memcore.memory_worker.prompts.schemas import valid_jakobson_output
from memcore.models import ModelRole
from memcore.storage.migrations import apply_migrations
from memcore.storage.sqlite import connect

PROMPT_VERSION = "2.0"
REQUIRED_V2_PROMPTS = {
    "memorist.jakobson_sentence_analysis",
    "memorist.memory_signal_routing_assist",
    "memorist.conative_instruction_extractor",
    "memorist.referential_context_extractor",
    "memorist.metalingual_policy_extractor",
    "memorist.emotive_preference_extractor",
    "memorist.poetic_style_extractor",
    "memorist.memory_consolidation_assist",
    "memorist.preflight_planning",
    "memorist.block_compaction",
    "memorist.import_reconstruction",
    "memorist.contradiction_detection",
    "memorist.privacy_sensitivity",
}


def test_prompt_registry_v2_lists_all_prompts() -> None:
    prompts = list_prompts()
    prompt_ids = {prompt.prompt_id for prompt in prompts}

    assert prompt_ids >= REQUIRED_V2_PROMPTS
    # The pack remains v2.0 while its contract-first runtime prompts have their
    # own immutable versions: Jakobson v3, semantic-candidate v1.1 and
    # preflight v2.1; historical semantic/preflight definitions remain replayable.
    assert all(
        prompt.prompt_version == PROMPT_VERSION
        for prompt in prompts
        if not (
            (
                prompt.prompt_id == "memorist.jakobson_sentence_analysis"
                and prompt.prompt_version == "3.0"
            )
            or (
                prompt.prompt_id == "memorist.semantic_candidate_analysis"
                and prompt.prompt_version in {"1.0", "1.1"}
            )
            or (
                prompt.prompt_id == "memorist.preflight_planning" and prompt.prompt_version == "2.1"
            )
        )
    )
    assert any(
        prompt.prompt_id == "memorist.jakobson_sentence_analysis" and prompt.prompt_version == "3.0"
        for prompt in prompts
    )
    assert any(
        prompt.prompt_id == "memorist.semantic_candidate_analysis"
        and prompt.prompt_version == "1.1"
        for prompt in prompts
    )
    assert get_prompt("memorist.semantic_candidate_analysis", "1.0").metadata.is_legacy is True
    assert get_prompt("memorist.preflight_planning", "2.0").metadata.is_legacy is True
    assert all(prompt.public_dict()["prompt_pack_id"] == PROMPT_PACK_ID for prompt in prompts)
    assert get_prompt("memorist.jakobson_sentence_analysis").stage == "jakobson_sentence_analysis"
    assert get_prompt("memorist.unit_analysis").metadata.is_legacy is True
    assert "You do not answer the user" in get_prompt("memorist.unit_analysis").full_system_prompt


def test_global_prompt_envelope_validation() -> None:
    output = _valid_routing_output()
    validate_prompt_output("memorist.memory_signal_routing_assist", output)

    del output["schema_version"]
    with pytest.raises(PromptValidationError, match="missing required"):
        validate_prompt_output("memorist.memory_signal_routing_assist", output)


def test_jakobson_prompt_schema() -> None:
    output = valid_jakobson_output()
    validate_prompt_output("memorist.jakobson_sentence_analysis", PROMPT_VERSION, output)

    output["sentences"][0]["dominant_function"] = "aggregate_only"
    with pytest.raises(PromptValidationError, match="invalid function"):
        validate_prompt_output("memorist.jakobson_sentence_analysis", PROMPT_VERSION, output)


def test_jakobson_persian_workflow_fixture() -> None:
    output = valid_jakobson_output()
    output["input_language"] = "fa"
    output["sentence_count"] = 2
    output["sentences"] = [
        _jakobson_sentence(
            1,
            "هوش مصنوعی باید هر دستور کاری را با evidence ذخیره کند.",
            "conative",
            ["referential"],
        ),
        _jakobson_sentence(
            2,
            "اصطلاح حافظه فعال را همیشه Active Memory ترجمه کن.",
            "conative",
            ["metalingual"],
        ),
    ]
    output["overall_summary"]["dominant_overall_function"] = "conative"
    output["overall_summary"]["secondary_overall_functions"] = ["referential", "metalingual"]

    validate_prompt_output("memorist.jakobson_sentence_analysis", PROMPT_VERSION, output)


def test_routing_assist_schema() -> None:
    validate_prompt_output("memorist.memory_signal_routing_assist", _valid_routing_output())

    output = _valid_routing_output()
    output["items"][0]["recommended_routes"][0]["route_type"] = "unsafe_override"
    with pytest.raises(PromptValidationError, match="route_type"):
        validate_prompt_output("memorist.memory_signal_routing_assist", output)


def test_conative_instruction_extractor() -> None:
    output = _extractor_output(
        "memorist.conative_instruction_extractor",
        "workflow_policy",
        "The AI must store evidence.",
    )
    output["items"][0]["obligation_strength"] = "mandatory"
    output["items"][0]["receiver_scope"] = "ai"

    validate_prompt_output("memorist.conative_instruction_extractor", output)


def test_referential_context_extractor() -> None:
    output = _extractor_output(
        "memorist.referential_context_extractor",
        "process_fact",
        "Jira is used for workflow tracking.",
    )

    validate_prompt_output("memorist.referential_context_extractor", output)


def test_metalingual_policy_extractor() -> None:
    output = _extractor_output(
        "memorist.metalingual_policy_extractor",
        "terminology_rule",
        "Use Active Memory as the English term.",
    )

    validate_prompt_output("memorist.metalingual_policy_extractor", output)


def test_emotive_preference_extractor() -> None:
    output = _extractor_output(
        "memorist.emotive_preference_extractor",
        "user_preference",
        "I prefer concise engineering updates.",
    )

    validate_prompt_output("memorist.emotive_preference_extractor", output)


def test_poetic_style_extractor() -> None:
    output = _extractor_output(
        "memorist.poetic_style_extractor",
        "style_policy",
        "Use a short slogan-like product tone.",
    )

    validate_prompt_output("memorist.poetic_style_extractor", output)


def test_consolidation_prompt() -> None:
    validate_prompt_output("memorist.memory_consolidation_assist", _valid_consolidation_output())

    output = _valid_consolidation_output()
    output["items"][0]["temporal_handling"]["preserve_old_version"] = False
    with pytest.raises(PromptValidationError, match="preserve old"):
        validate_prompt_output("memorist.memory_consolidation_assist", output)


def test_preflight_prompt_budget_rejection() -> None:
    input_payload = _valid_preflight_input(max_tokens=25)
    output = _valid_preflight_output(estimated_tokens=100)

    with pytest.raises(PromptValidationError, match="exceeds budget"):
        validate_prompt_execution(
            "memorist.preflight_planning",
            PROMPT_VERSION,
            input_payload,
            output,
        )


def test_block_compaction_requires_sources() -> None:
    output = _valid_block_compaction_output()
    output["items"][0]["source_memory_uuids"] = []

    with pytest.raises(PromptValidationError, match="source_memory_uuids"):
        validate_prompt_output("memorist.block_compaction", output)


def test_import_reconstruction_untrusted() -> None:
    output = _valid_import_reconstruction_output()
    validate_prompt_output("memorist.import_reconstruction", output)

    output["items"][0]["trust_level"] = "trusted_current"
    with pytest.raises(PromptValidationError, match="historical_untrusted"):
        validate_prompt_output("memorist.import_reconstruction", output)


def test_contradiction_detection() -> None:
    validate_prompt_output("memorist.contradiction_detection", _valid_contradiction_output())

    output = _valid_contradiction_output()
    output["items"][0]["relation"] = "same_enough"
    with pytest.raises(PromptValidationError, match="relation"):
        validate_prompt_output("memorist.contradiction_detection", output)


def test_privacy_sensitivity_restricts_high_sensitivity() -> None:
    output = _valid_privacy_output()
    validate_prompt_output("memorist.privacy_sensitivity", output)

    output["items"][0]["allowed_retrieval"] = "normal"
    with pytest.raises(PromptValidationError, match="restrict retrieval"):
        validate_prompt_output("memorist.privacy_sensitivity", output)


def test_prompt_execution_audit_linkage(tmp_path: Path) -> None:
    db_path = tmp_path / "memorist.sqlite"
    connection = connect(db_path)
    try:
        apply_migrations(connection)
        output = _extractor_output(
            "memorist.conative_instruction_extractor",
            "workflow_policy",
            "The AI must store evidence.",
        )
        record = PromptExecutionRepository(connection).record_execution(
            prompt_id="memorist.conative_instruction_extractor",
            prompt_version=PROMPT_VERSION,
            model_role=ModelRole.MEMORY_EXTRACTION,
            provider_type="deterministic",
            model_name="deterministic-extractor-v2",
            input_value={"route_uuid": "route-1", "annotation_uuid": "annotation-1"},
            raw_output_value=output,
            validated_output_value=output,
            status="ok",
            annotation_uuid="annotation-1",
            route_uuid="route-1",
        )
        row = connection.execute(
            """
            SELECT prompt_id, prompt_version, model_role, provider_type, output_hash,
                   raw_output_ijson, validated_output_ijson
            FROM prompt_execution_runs
            WHERE prompt_execution_uuid = ?
            """,
            (record["prompt_execution_uuid"],),
        ).fetchone()

        assert row is not None
        assert row["prompt_id"] == "memorist.conative_instruction_extractor"
        assert row["prompt_version"] == PROMPT_VERSION
        assert row["model_role"] == "memory_extraction"
        assert row["provider_type"] == "deterministic"
        assert len(row["output_hash"]) == 64
        assert row["raw_output_ijson"] is not None
        assert row["validated_output_ijson"] is not None
    finally:
        connection.close()


def test_prompt_role_mapping() -> None:
    expected = {
        "memorist.preflight_planning": ["preflight"],
        "memorist.jakobson_sentence_analysis": [
            "memory_extraction",
            "import_reconstruction",
        ],
        "memorist.memory_signal_routing_assist": ["memory_extraction"],
        "memorist.conative_instruction_extractor": ["memory_extraction"],
        "memorist.referential_context_extractor": ["memory_extraction"],
        "memorist.metalingual_policy_extractor": ["memory_extraction"],
        "memorist.emotive_preference_extractor": ["memory_extraction"],
        "memorist.poetic_style_extractor": ["memory_extraction"],
        "memorist.memory_consolidation_assist": ["memory_extraction"],
        "memorist.block_compaction": ["block_compaction", "memory_extraction"],
        "memorist.import_reconstruction": ["import_reconstruction", "memory_extraction"],
        "memorist.privacy_sensitivity": ["privacy_sensitivity", "memory_extraction"],
    }

    for prompt_id, roles in expected.items():
        assert [role.value for role in get_prompt(prompt_id).allowed_model_roles] == roles


def test_no_main_chat_implicit_prompt_use() -> None:
    for prompt in list_prompts():
        assert ModelRole.MAIN_CHAT_OBSERVED not in prompt.allowed_model_roles


def test_validate_prompt_input_and_render_prompt() -> None:
    payload = _valid_preflight_input(max_tokens=50)
    result = validate_prompt_input("memorist.preflight_planning", PROMPT_VERSION, payload)
    rendered = render_prompt(
        "memorist.preflight_planning",
        PROMPT_VERSION,
        {"PAYLOAD_IJSON": payload},
    )

    assert result.valid is True
    assert "memorist.preflight_planning" in rendered
    assert "current_user_message" in rendered


def test_legacy_prompt_invocation_wrapper_records_both_tables(tmp_path: Path) -> None:
    db_path = tmp_path / "memorist.sqlite"
    connection = connect(db_path)
    try:
        apply_migrations(connection)
        output = _valid_routing_output()
        record = PromptInvocationRepository(connection).record_invocation(
            "memorist.memory_signal_routing_assist",
            PROMPT_VERSION,
            provider_type="deterministic",
            input_value={"annotation_uuid": "annotation-1"},
            output_value=output,
        )

        assert record["prompt_execution_uuid"]
        legacy = connection.execute(
            """
            SELECT prompt_id, prompt_version
            FROM memory_prompt_invocations
            WHERE prompt_invocation_uuid = ?
            """,
            (record["prompt_invocation_uuid"],),
        ).fetchone()
        assert legacy is not None
        assert legacy["prompt_version"] == PROMPT_VERSION
    finally:
        connection.close()


def _base(prompt_id: str) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "prompt_id": prompt_id,
        "prompt_version": PROMPT_VERSION,
        "status": "ok",
        "warnings": [],
        "items": [],
    }


def _valid_routing_output() -> dict[str, Any]:
    output = _base("memorist.memory_signal_routing_assist")
    output["items"] = [
        {
            "annotation_uuid": "annotation-1",
            "recommended_routes": [
                {
                    "route_type": "workflow_policy",
                    "extractor_id": "conative_instruction_extractor",
                    "priority": 90,
                    "confidence": 0.88,
                    "reason": "The sentence gives a durable workflow instruction.",
                }
            ],
            "reject_extraction": False,
            "manual_review": False,
        }
    ]
    return output


def _extractor_output(prompt_id: str, candidate_type: str, quote: str) -> dict[str, Any]:
    output = _base(prompt_id)
    output["items"] = [
        {
            "annotation_uuid": "annotation-1",
            "route_uuid": "route-1",
            "candidate_type": candidate_type,
            "canonical_key": f"project.{candidate_type}.sample",
            "subject": "project",
            "predicate": "has_rule",
            "object": quote,
            "value": {"text": quote},
            "natural_language_summary": quote,
            "confidence": 0.9,
            "importance": 0.7,
            "evidence": [
                {
                    "annotation_uuid": "annotation-1",
                    "route_uuid": "route-1",
                    "unit_uuid": "unit-1",
                    "message_uuid": "message-1",
                    "quote": quote,
                    "span_start": 0,
                    "span_end": len(quote),
                }
            ],
            "rejection_reason": None,
        }
    ]
    return output


def _valid_consolidation_output() -> dict[str, Any]:
    output = _base("memorist.memory_consolidation_assist")
    output["items"] = [
        {
            "candidate_uuid": "candidate-1",
            "recommended_operation": "UPDATE",
            "target_memory_uuid": "memory-1",
            "reason_code": "newer_preference",
            "confidence": 0.82,
            "temporal_handling": {
                "preserve_old_version": True,
                "new_valid_from": "2026-06-29T00:00:00Z",
                "old_valid_until": "2026-06-28T23:59:59Z",
            },
            "scope_handling": {"scope_ok": True, "recommended_scope": "project"},
            "manual_review_reason": None,
        }
    ]
    return output


def _valid_preflight_input(max_tokens: int) -> dict[str, Any]:
    return {
        "current_user_message": "Use the memory engine safely.",
        "main_chat_model": {
            "provider": "openwebui",
            "model_name": "user-selected",
            "context_window": 8192,
        },
        "attachment_budget": {"max_tokens": max_tokens, "mode": "standard"},
        "active_blocks": [],
        "retrieval_candidates": [],
        "conflicts": [],
        "security_warnings": [],
    }


def _valid_preflight_output(estimated_tokens: int = 24) -> dict[str, Any]:
    output = _base("memorist.preflight_planning")
    output["items"] = [
        {
            "attachment_mode": "lite",
            "selected_memory_ids": ["memory-1"],
            "excluded_memory_ids": [],
            "trusted_directive_ids": [],
            "ordinary_memory_ids": ["memory-1"],
            "conflict_ids": [],
            "compression_strategy": "source_linked_brief",
            "abstain_reason": None,
            "security_notes": [],
            "estimated_tokens": estimated_tokens,
        }
    ]
    return output


def _valid_block_compaction_output() -> dict[str, Any]:
    output = _base("memorist.block_compaction")
    output["items"] = [
        {
            "block_type": "ProjectContextBlock",
            "block_text": "Project uses FastAPI.",
            "source_memory_uuids": ["memory-1"],
            "conflict_memory_uuids": [],
            "uncertainty_notes": [],
            "token_estimate": 8,
            "coverage": {"included_count": 1, "excluded_count": 0, "exclusion_reasons": []},
        }
    ]
    return output


def _valid_import_reconstruction_output() -> dict[str, Any]:
    output = _base("memorist.import_reconstruction")
    output["items"] = [
        {
            "conversation_title": None,
            "message_tree_valid": True,
            "detected_branches": [],
            "role_repairs": [],
            "timestamp_repairs": [],
            "unsupported_content_parts": [],
            "candidate_processing_recommendation": "manual_review",
            "trust_level": "historical_untrusted",
            "notes": ["Imported content remains untrusted."],
        }
    ]
    return output


def _valid_contradiction_output() -> dict[str, Any]:
    output = _base("memorist.contradiction_detection")
    output["items"] = [
        {
            "relation": "corrects",
            "target_memory_version_uuid": "version-1",
            "confidence": 0.8,
            "reason_code": "explicit_user_correction",
            "recommended_action": "SUPERSEDE",
            "requires_user_confirmation": False,
        }
    ]
    return output


def _valid_privacy_output() -> dict[str, Any]:
    output = _base("memorist.privacy_sensitivity")
    output["items"] = [
        {
            "sensitivity_level": "high",
            "categories": ["personal"],
            "requires_confirmation": True,
            "allowed_storage": "manual_review",
            "allowed_retrieval": "never_auto_attach",
            "redaction_recommendation": None,
            "reason": "Contains sensitive personal material.",
        }
    ]
    return output


def _jakobson_sentence(
    sentence_id: int,
    text: str,
    dominant_function: str,
    secondary_functions: list[str],
) -> dict[str, Any]:
    return {
        "id": sentence_id,
        "text": text,
        "six_factors": {
            "sender_addresser": _factor("user", text, "high"),
            "receiver_addressee": _factor("AI or product team", text, "medium"),
            "message": _factor(text, text, "high"),
            "context_referent": _factor("Memorist workflow", text, "high"),
            "code": _factor("Persian; product workflow terminology", text, "high"),
            "contact_channel": _factor("chat", "chat message", "medium"),
        },
        "dominant_function": dominant_function,
        "secondary_functions": secondary_functions,
        "function_reason": "The sentence is a product workflow instruction.",
        "notes": "",
    }


def _factor(value: str, evidence: str, confidence: str) -> dict[str, str]:
    return {"value": value, "evidence": evidence, "confidence": confidence}
