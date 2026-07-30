from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from memcore.memory_worker.prompts.contracts import get_contract
from memcore.memory_worker.prompts.schemas import validate_jakobson_output
from memcore.memory_worker.prompts.versions import (
    JAKOBSON_SENTENCE_ANALYSIS_PROMPT_ID,
    SEMANTIC_CANDIDATE_ANALYSIS_PROMPT_ID,
)


class PromptValidationError(ValueError):
    """Raised when a memory-worker prompt payload violates its contract."""


ALLOWED_STATUS = {"ok", "abstain", "reject", "error"}
ALLOWED_ROUTE_TYPES = {
    "ignore",
    "project_context",
    "workflow_policy",
    "team_obligation",
    "prompt_instruction",
    "style_policy",
    "terminology_rule",
    "user_preference",
    "emotional_stance",
    "process_fact",
    "jira_configuration",
    "task_constraint",
    "resource_reference",
    "privacy_review",
    "manual_review",
}
EXTRACTOR_CANDIDATE_TYPES = {
    "memorist.conative_instruction_extractor": {
        "workflow_policy",
        "team_obligation",
        "prompt_instruction",
        "task_constraint",
        "jira_configuration",
    },
    "memorist.referential_context_extractor": {
        "project_context",
        "process_fact",
        "jira_configuration",
        "resource_reference",
        "semantic_fact",
    },
    "memorist.metalingual_policy_extractor": {
        "terminology_rule",
        "style_policy",
        "prompt_instruction",
        "naming_rule",
    },
    "memorist.emotive_preference_extractor": {
        "user_preference",
        "emotional_stance",
        "quality_feedback",
        "avoidance_preference",
    },
    "memorist.poetic_style_extractor": {
        "style_policy",
        "branding_style",
        "slogan_preference",
        "rhetorical_pattern",
    },
}


def validate_common_envelope(
    *,
    prompt_id: str,
    prompt_version: str,
    output: Mapping[str, Any],
) -> None:
    required = {"schema_version", "prompt_id", "prompt_version", "status", "warnings", "items"}
    missing = sorted(required - set(output))
    if missing:
        raise PromptValidationError(f"prompt output missing required fields: {missing}")
    if output["schema_version"] != "1.0":
        raise PromptValidationError("schema_version must be '1.0'")
    if output["prompt_id"] != prompt_id:
        raise PromptValidationError("prompt_id does not match requested prompt")
    if output["prompt_version"] != prompt_version:
        raise PromptValidationError("prompt_version does not match registry version")
    if output["status"] not in ALLOWED_STATUS:
        raise PromptValidationError("status must be ok, abstain, reject, or error")
    if not isinstance(output["warnings"], list):
        raise PromptValidationError("warnings must be a list")
    if not isinstance(output["items"], list):
        raise PromptValidationError("items must be a list")


def validate_required_input_keys(payload: Mapping[str, Any], required_keys: list[str]) -> None:
    missing = sorted(set(required_keys) - set(payload))
    if missing:
        raise PromptValidationError(f"prompt input missing required fields: {missing}")


def validate_prompt_specific_output(
    prompt_id: str,
    output: dict[str, Any],
    version: str | None = None,
) -> None:
    if prompt_id == JAKOBSON_SENTENCE_ANALYSIS_PROMPT_ID:
        _validate_jakobson_prompt_output(output, version)
    elif prompt_id == SEMANTIC_CANDIDATE_ANALYSIS_PROMPT_ID:
        _validate_semantic_candidate_output(output, version)
    elif prompt_id == "memorist.unit_analysis":
        _validate_unit_analysis(output)
    elif prompt_id == "memorist.memory_signal_routing_assist":
        _validate_routing_assist(output)
    elif prompt_id in EXTRACTOR_CANDIDATE_TYPES:
        _validate_route_extractor(prompt_id, output)
    elif prompt_id == "memorist.memory_consolidation_assist":
        _validate_consolidation(output)
    elif prompt_id == "memorist.preflight_planning":
        _validate_preflight(output)
    elif prompt_id == "memorist.block_compaction":
        _validate_block_compaction(output)
    elif prompt_id == "memorist.import_reconstruction":
        _validate_import_reconstruction(output)
    elif prompt_id == "memorist.contradiction_detection":
        _validate_contradiction(output)
    elif prompt_id == "memorist.privacy_sensitivity":
        _validate_privacy(output)


def validate_prompt_output_against_input(
    *,
    prompt_id: str,
    input_payload: Mapping[str, Any],
    output: dict[str, Any],
) -> None:
    if prompt_id == JAKOBSON_SENTENCE_ANALYSIS_PROMPT_ID:
        sentences = input_payload.get("sentences")
        items = output.get("items")
        if not isinstance(sentences, list):
            raise PromptValidationError("jakobson input sentences must be a list")
        if not isinstance(items, list):
            raise PromptValidationError("jakobson output items must be a list")
        if output.get("status") != "ok":
            if items:
                raise PromptValidationError("non-ok jakobson output must not contain items")
        else:
            if len(items) != len(sentences):
                raise PromptValidationError("jakobson items must match input sentence count")
            for index, (sentence, item) in enumerate(zip(sentences, items, strict=True)):
                if not isinstance(sentence, Mapping) or not isinstance(item, Mapping):
                    raise PromptValidationError(
                        f"jakobson sentence binding is invalid at index {index}"
                    )
                if item.get("id") != sentence.get("id"):
                    raise PromptValidationError(
                        f"jakobson item id does not match input at index {index}"
                    )
                if item.get("text") != sentence.get("text"):
                    raise PromptValidationError(
                        f"jakobson item text does not match input at index {index}"
                    )
    if prompt_id == SEMANTIC_CANDIDATE_ANALYSIS_PROMPT_ID:
        from memcore.memory_worker.semantic_contract import validate_semantic_binding

        try:
            validate_semantic_binding(input_payload, output)
        except ValueError as error:
            raise PromptValidationError(str(error)) from error
    if prompt_id == "memorist.preflight_planning":
        budget = input_payload.get("attachment_budget")
        max_tokens = budget.get("max_tokens") if isinstance(budget, Mapping) else None
        if isinstance(max_tokens, int):
            for item in _items(output):
                if int(item.get("estimated_tokens", 0)) > max_tokens:
                    raise PromptValidationError("preflight estimated_tokens exceeds budget")
    if prompt_id == "memorist.block_compaction":
        source_memories = input_payload.get("source_memories")
        source_ids: set[str] = set()
        if isinstance(source_memories, list):
            source_ids = {
                str(item["memory_uuid"])
                for item in source_memories
                if isinstance(item, Mapping) and item.get("memory_uuid") is not None
            }
        if source_ids:
            for item in _items(output):
                for memory_uuid in item.get("source_memory_uuids", []):
                    if str(memory_uuid) not in source_ids:
                        raise PromptValidationError(
                            "block compaction output references unknown source"
                        )


def _validate_jakobson_prompt_output(output: dict[str, Any], version: str | None = None) -> None:
    contract = (
        get_contract(JAKOBSON_SENTENCE_ANALYSIS_PROMPT_ID, version) if version is not None else None
    )
    if contract is not None:
        issues = contract.validate(output)
        if issues:
            first = issues[0]
            raise PromptValidationError(f"{first['path']}: {first['message']}")
        return
    try:
        validate_jakobson_output(output)
    except ValueError as error:
        raise PromptValidationError(str(error)) from error


def _validate_semantic_candidate_output(output: dict[str, Any], version: str | None = None) -> None:
    contract = (
        get_contract(SEMANTIC_CANDIDATE_ANALYSIS_PROMPT_ID, version)
        if version is not None
        else None
    )
    if contract is None:
        raise PromptValidationError("semantic candidate contract is unavailable")
    issues = contract.validate(output)
    if issues:
        first = issues[0]
        raise PromptValidationError(f"{first['path']}: {first['message']}")


def _validate_unit_analysis(output: dict[str, Any]) -> None:
    for item in _items(output):
        _require_string(item, "unit_uuid")
        if item.get("memory_signal") not in {"none", "weak", "medium", "strong"}:
            raise PromptValidationError("unit_analysis memory_signal is invalid")
        evidence = _require_mapping(item, "evidence")
        _require_string(evidence, "quote")
        _require_int(evidence, "span_start")
        _require_int(evidence, "span_end")


def _validate_routing_assist(output: dict[str, Any]) -> None:
    for item in _items(output):
        _require_string(item, "annotation_uuid")
        _require_bool(item, "reject_extraction")
        _require_bool(item, "manual_review")
        routes = item.get("recommended_routes")
        if not isinstance(routes, list):
            raise PromptValidationError("recommended_routes must be a list")
        for route in routes:
            if not isinstance(route, Mapping):
                raise PromptValidationError("recommended_routes entries must be objects")
            if route.get("route_type") not in ALLOWED_ROUTE_TYPES:
                raise PromptValidationError("route_type is invalid")
            _require_string(route, "extractor_id")
            _require_int(route, "priority")
            _require_number(route, "confidence")
            _require_string(route, "reason")


def _validate_route_extractor(prompt_id: str, output: dict[str, Any]) -> None:
    allowed_types = EXTRACTOR_CANDIDATE_TYPES[prompt_id]
    for item in _items(output):
        rejected = output["status"] in {"abstain", "reject"} or item.get("rejection_reason")
        candidate_type = item.get("candidate_type")
        if candidate_type not in allowed_types:
            raise PromptValidationError("candidate_type is invalid for extractor")
        _require_string(item, "annotation_uuid")
        _require_string(item, "route_uuid")
        _require_string(item, "canonical_key")
        _require_string(item, "natural_language_summary")
        _require_number(item, "confidence")
        _require_number(item, "importance")
        evidence = item.get("evidence")
        if not isinstance(evidence, list) or (not evidence and not rejected):
            raise PromptValidationError("extractor candidates require evidence")
        for span in evidence:
            _validate_candidate_evidence(span)


def _validate_candidate_evidence(span: Any) -> None:
    if not isinstance(span, Mapping):
        raise PromptValidationError("candidate evidence entries must be objects")
    _require_string(span, "annotation_uuid")
    _require_string(span, "route_uuid")
    _require_string(span, "unit_uuid")
    _require_string(span, "message_uuid")
    _require_string(span, "quote")
    _require_int(span, "span_start")
    _require_int(span, "span_end")


def _validate_consolidation(output: dict[str, Any]) -> None:
    destructive = {"UPDATE", "SUPERSEDE", "CONTRADICT", "RETRACT"}
    allowed = destructive | {"ADD", "REINFORCE", "NOOP", "REJECT", "MANUAL_REVIEW"}
    for item in _items(output):
        operation = item.get("recommended_operation")
        if operation not in allowed:
            raise PromptValidationError("recommended_operation is invalid")
        temporal = _require_mapping(item, "temporal_handling")
        if operation in destructive and temporal.get("preserve_old_version") is not True:
            raise PromptValidationError("destructive consolidation must preserve old versions")
        scope = _require_mapping(item, "scope_handling")
        if scope.get("recommended_scope") not in {"session", "project", "workspace", "user_local"}:
            raise PromptValidationError("recommended_scope is invalid")


def _validate_preflight(output: dict[str, Any]) -> None:
    forbidden_keys = {
        "current_user_message",
        "rewritten_prompt",
        "mutated_prompt",
        "user_prompt",
        "prompt_text",
    }
    for item in _items(output):
        if forbidden_keys & set(item):
            raise PromptValidationError("preflight output must not mutate or echo the user prompt")
        if item.get("attachment_mode") not in {"disabled", "lite", "standard", "full"}:
            raise PromptValidationError("attachment_mode is invalid")
        if item.get("compression_strategy") not in {
            "none",
            "summarize",
            "bullet_compact",
            "source_linked_brief",
        }:
            raise PromptValidationError("compression_strategy is invalid")
        _require_int(item, "estimated_tokens")


def _validate_block_compaction(output: dict[str, Any]) -> None:
    allowed_block_types = {
        "UserProfileBlock",
        "ProjectContextBlock",
        "StylePolicyBlock",
        "PromptRulesBlock",
        "CurrentSessionStateBlock",
        "SafetyPrivacyBlock",
    }
    for item in _items(output):
        if item.get("block_type") not in allowed_block_types:
            raise PromptValidationError("block_type is invalid")
        sources = item.get("source_memory_uuids")
        block_text = item.get("block_text")
        if output["status"] == "ok" and block_text and not sources:
            raise PromptValidationError("block compaction requires source_memory_uuids")
        if not isinstance(sources, list):
            raise PromptValidationError("source_memory_uuids must be a list")
        _require_int(item, "token_estimate")
        _require_mapping(item, "coverage")


def _validate_import_reconstruction(output: dict[str, Any]) -> None:
    for item in _items(output):
        if item.get("trust_level") != "historical_untrusted":
            raise PromptValidationError(
                "import reconstruction trust_level must be historical_untrusted"
            )
        if item.get("candidate_processing_recommendation") not in {
            "none",
            "index_only",
            "extract_candidates",
            "manual_review",
        }:
            raise PromptValidationError("candidate_processing_recommendation is invalid")


def _validate_contradiction(output: dict[str, Any]) -> None:
    for item in _items(output):
        if item.get("relation") not in {
            "reinforces",
            "updates",
            "contradicts",
            "corrects",
            "retracts",
            "unrelated",
            "unclear",
        }:
            raise PromptValidationError("contradiction relation is invalid")
        if item.get("recommended_action") not in {
            "REINFORCE",
            "UPDATE",
            "SUPERSEDE",
            "CONTRADICT",
            "RETRACT",
            "MANUAL_REVIEW",
            "NOOP",
        }:
            raise PromptValidationError("recommended_action is invalid")


def _validate_privacy(output: dict[str, Any]) -> None:
    for item in _items(output):
        level = item.get("sensitivity_level")
        if level not in {"none", "low", "medium", "high"}:
            raise PromptValidationError("sensitivity_level is invalid")
        if item.get("allowed_storage") not in {
            "allow",
            "allow_local_only",
            "manual_review",
            "reject",
        }:
            raise PromptValidationError("allowed_storage is invalid")
        if item.get("allowed_retrieval") not in {"normal", "restricted", "never_auto_attach"}:
            raise PromptValidationError("allowed_retrieval is invalid")
        if level == "high":
            if item.get("requires_confirmation") is not True:
                raise PromptValidationError("high sensitivity requires confirmation")
            if item.get("allowed_storage") == "allow":
                raise PromptValidationError("high sensitivity cannot be unrestricted storage")
            if item.get("allowed_retrieval") not in {"restricted", "never_auto_attach"}:
                raise PromptValidationError("high sensitivity must restrict retrieval")


def _items(output: dict[str, Any]) -> list[dict[str, Any]]:
    validated_items: list[dict[str, Any]] = []
    for item in output["items"]:
        if not isinstance(item, dict):
            raise PromptValidationError("items must contain objects")
        validated_items.append(item)
    return validated_items


def _require_mapping(item: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = item.get(key)
    if not isinstance(value, Mapping):
        raise PromptValidationError(f"{key} must be an object")
    return value


def _require_string(item: Mapping[str, Any], key: str) -> str:
    value = item.get(key)
    if not isinstance(value, str) or value == "":
        raise PromptValidationError(f"{key} must be a non-empty string")
    return value


def _require_int(item: Mapping[str, Any], key: str) -> int:
    value = item.get(key)
    if not isinstance(value, int):
        raise PromptValidationError(f"{key} must be an integer")
    return value


def _require_number(item: Mapping[str, Any], key: str) -> float:
    value = item.get(key)
    if not isinstance(value, int | float):
        raise PromptValidationError(f"{key} must be a number")
    if not 0 <= float(value) <= 1 and key in {"confidence", "importance"}:
        raise PromptValidationError(f"{key} must be between 0 and 1")
    return float(value)


def _require_bool(item: Mapping[str, Any], key: str) -> bool:
    value = item.get(key)
    if not isinstance(value, bool):
        raise PromptValidationError(f"{key} must be a boolean")
    return value
