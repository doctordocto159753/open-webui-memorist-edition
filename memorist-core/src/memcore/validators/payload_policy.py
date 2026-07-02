from collections.abc import Mapping
from typing import Any

from memcore.validators.ijson import dump_ijson, load_ijson, validate_ijson_value

IJSON_REQUIRED_FIELDS = {
    "payload_ijson",
    "raw_payload_ijson",
    "snapshot_ijson",
    "pricing_ijson",
    "metadata_ijson",
    "source_memory_uuids_ijson",
    "source_block_uuids_ijson",
    "source_fact_edge_uuids_ijson",
    "ijson_attachment",
    "active_constraints_ijson",
    "unresolved_questions_ijson",
    "recent_entities_ijson",
    "recent_memory_uuids_ijson",
    "config_snapshot_ijson",
    "filters_ijson",
    "exclusion_reasons_ijson",
    "score_trace_ijson",
    "embedding_ijson",
    "builder_policy_ijson",
    "structured_value_ijson",
    "raw_output_ijson",
    "validation_errors_ijson",
    "evidence_ijson",
    "proposed_value_ijson",
    "impact_preview_ijson",
    "requested_scope_ijson",
    "policy_decision_ijson",
    "erased_record_counts_ijson",
    "retained_record_counts_ijson",
    "exceptions_ijson",
    "verification_ijson",
    "options_ijson",
    "report_ijson",
    "details_ijson",
    "normalized_payload_ijson",
    "normalized_conversation_ijson",
    "manifest_ijson",
}

TOP_LEVEL_OBJECT_REQUIRED_FIELDS = frozenset({"payload_ijson", "ijson_attachment"})


class PayloadPolicyError(ValueError):
    """Raised when a payload field violates Memorist storage policy."""


def is_ijson_field(field_name: str) -> bool:
    return field_name.endswith("_ijson") or field_name in IJSON_REQUIRED_FIELDS


def prepare_ijson_field(field_name: str, value: Any) -> str | None:
    if value is None:
        return None

    if isinstance(value, str):
        loaded_value = load_ijson(value)
    else:
        validate_ijson_value(value)
        loaded_value = value

    if field_name in TOP_LEVEL_OBJECT_REQUIRED_FIELDS and not isinstance(loaded_value, dict):
        raise PayloadPolicyError(f"{field_name} must contain a top-level I-JSON object")

    return dump_ijson(loaded_value)


def prepare_repository_values(values: Mapping[str, Any]) -> dict[str, Any]:
    prepared_values: dict[str, Any] = {}
    for field_name, value in values.items():
        if is_ijson_field(field_name):
            prepared_values[field_name] = prepare_ijson_field(field_name, value)
        else:
            prepared_values[field_name] = value
    return prepared_values
