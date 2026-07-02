from __future__ import annotations

from pydantic import BaseModel

from memcore.models import ModelRole


class ModelRoleSpec(BaseModel):
    role: ModelRole
    title: str
    required: bool
    controlled_by_memorist: bool
    lifecycle: str
    blocks_main_request: bool
    fail_open: bool
    sends_user_content: bool
    sends_memory_content: bool
    default_mode: str
    data_sent: list[str]
    safe_beta_default: str
    description: str


MODEL_ROLE_SPECS: dict[ModelRole, ModelRoleSpec] = {
    ModelRole.MAIN_CHAT_OBSERVED: ModelRoleSpec(
        role=ModelRole.MAIN_CHAT_OBSERVED,
        title="Main Chat Model",
        required=True,
        controlled_by_memorist=False,
        lifecycle="observed in Open WebUI; Memorist never controls the main chat request",
        blocks_main_request=False,
        fail_open=True,
        sends_user_content=False,
        sends_memory_content=False,
        default_mode="observed_from_openwebui",
        data_sent=["metadata_only_when_available"],
        safe_beta_default="selected in Open WebUI; not controlled by Memorist",
        description="The user-selected Open WebUI chat model. Memorist observes metadata only.",
    ),
    ModelRole.PREFLIGHT: ModelRoleSpec(
        role=ModelRole.PREFLIGHT,
        title="Pre-flight Model",
        required=True,
        controlled_by_memorist=True,
        lifecycle="optional bounded step before the main chat request",
        blocks_main_request=False,
        fail_open=True,
        sends_user_content=True,
        sends_memory_content=True,
        default_mode="deterministic_preflight",
        data_sent=["current_user_text", "retrieval_candidates", "active_constraints"],
        safe_beta_default="deterministic_preflight local fail-open",
        description=(
            "Builds or validates memory context before the main request; failures fail open."
        ),
    ),
    ModelRole.MEMORY_EXTRACTION: ModelRoleSpec(
        role=ModelRole.MEMORY_EXTRACTION,
        title="Memory Extraction Model",
        required=True,
        controlled_by_memorist=True,
        lifecycle="asynchronous job after the assistant response is captured",
        blocks_main_request=False,
        fail_open=True,
        sends_user_content=True,
        sends_memory_content=False,
        default_mode="deterministic_extraction",
        data_sent=["captured_user_text", "captured_assistant_text", "sentence_units"],
        safe_beta_default="deterministic_extraction local async",
        description="Extracts evidence-grounded memories from captured conversation text.",
    ),
    ModelRole.EMBEDDING: ModelRoleSpec(
        role=ModelRole.EMBEDDING,
        title="Embedding Model",
        required=True,
        controlled_by_memorist=True,
        lifecycle="asynchronous/re-indexable embedding generation; optional in Lite mode",
        blocks_main_request=False,
        fail_open=True,
        sends_user_content=True,
        sends_memory_content=True,
        default_mode="disabled_lite",
        data_sent=["memory_text", "query_text"],
        safe_beta_default="disabled in Lite until a local embedding profile is configured",
        description="Embeds memory/query text for semantic retrieval and can be re-indexed.",
    ),
    ModelRole.IMPORT_RECONSTRUCTION: ModelRoleSpec(
        role=ModelRole.IMPORT_RECONSTRUCTION,
        title="Import Reconstruction Model",
        required=False,
        controlled_by_memorist=True,
        lifecycle="optional import job for reconstructing incomplete archives",
        blocks_main_request=False,
        fail_open=True,
        sends_user_content=True,
        sends_memory_content=False,
        default_mode="inherits_memory_extraction",
        data_sent=["imported_historical_text"],
        safe_beta_default="disabled unless explicitly enabled",
        description="Optional model role for reconstructing imported conversation fragments.",
    ),
    ModelRole.HIGH_CONFIDENCE_EXTRACTION: ModelRoleSpec(
        role=ModelRole.HIGH_CONFIDENCE_EXTRACTION,
        title="High-confidence Extraction Model",
        required=False,
        controlled_by_memorist=True,
        lifecycle="optional second-pass extraction for high-value or ambiguous evidence",
        blocks_main_request=False,
        fail_open=True,
        sends_user_content=True,
        sends_memory_content=False,
        default_mode="inherits_memory_extraction",
        data_sent=["eligible_sentence_units", "candidate_evidence"],
        safe_beta_default="inherits deterministic memory extraction",
        description="Optional stricter extraction pass used when higher confidence is required.",
    ),
    ModelRole.BLOCK_COMPACTION: ModelRoleSpec(
        role=ModelRole.BLOCK_COMPACTION,
        title="Block Compaction Model",
        required=False,
        controlled_by_memorist=True,
        lifecycle="optional background compaction of Active Memory Blocks",
        blocks_main_request=False,
        fail_open=True,
        sends_user_content=False,
        sends_memory_content=True,
        default_mode="inherits_memory_extraction",
        data_sent=["approved_memory_summaries", "source_memory_uuids"],
        safe_beta_default="disabled unless explicitly enabled",
        description="Optional role for compacting derived Active Memory Blocks.",
    ),
    ModelRole.PRIVACY_SENSITIVITY: ModelRoleSpec(
        role=ModelRole.PRIVACY_SENSITIVITY,
        title="Privacy Sensitivity Model",
        required=False,
        controlled_by_memorist=True,
        lifecycle="optional pre-storage classification for sensitive memory candidates",
        blocks_main_request=False,
        fail_open=True,
        sends_user_content=True,
        sends_memory_content=True,
        default_mode="inherits_memory_extraction",
        data_sent=["candidate_memory_text", "evidence_spans"],
        safe_beta_default="deterministic/local policy classifier",
        description="Optional role for sensitivity classification before storage or retrieval use.",
    ),
}


def list_role_specs() -> list[dict[str, object]]:
    return [
        spec.model_dump(mode="json")
        for spec in sorted(MODEL_ROLE_SPECS.values(), key=lambda item: item.role.value)
    ]
