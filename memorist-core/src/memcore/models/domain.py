from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator

from memcore.validators.ijson import validate_ijson_text


def new_uuid() -> str:
    return str(uuid4())


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class SessionStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"
    DELETED = "deleted"


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


class CreatorType(StrEnum):
    USER = "user"
    MODEL = "model"
    TOOL = "tool"
    MEMORY_AUGMENTED_MODEL = "memory_augmented_model"
    SYSTEM = "system"


class ProcessingStatus(StrEnum):
    PENDING = "pending"
    UNITIZED = "unitized"
    GATED = "gated"
    ANALYZED = "analyzed"
    CANDIDATES_CREATED = "candidates_created"
    CONSOLIDATED = "consolidated"
    PROJECTED = "projected"
    AVAILABLE = "available"
    FAILED = "failed"


class ModelRole(StrEnum):
    MAIN_CHAT = "main_chat_observed"
    MAIN_CHAT_OBSERVED = "main_chat_observed"
    MEMORY_EXTRACTION = "memory_extraction"
    EMBEDDING = "embedding"
    PREFLIGHT = "preflight"
    HIGH_CONFIDENCE_EXTRACTION = "high_confidence_extraction"
    IMPORT_RECONSTRUCTION = "import_reconstruction"
    BLOCK_COMPACTION = "block_compaction"
    PRIVACY_SENSITIVITY = "privacy_sensitivity"

    @classmethod
    def _missing_(cls, value: object) -> "ModelRole | None":
        aliases = {
            "main_chat": cls.MAIN_CHAT_OBSERVED,
            "memory_worker": cls.MEMORY_EXTRACTION,
            "cheap_extraction": cls.MEMORY_EXTRACTION,
        }
        if isinstance(value, str):
            return aliases.get(value)
        return None


class JobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    DEAD = "dead"
    CANCELLED = "cancelled"


class AttachmentMode(StrEnum):
    LITE = "lite"
    STANDARD = "standard"
    FULL = "full"


class MemoryBlockType(StrEnum):
    USER_PROFILE = "UserProfileBlock"
    PROJECT_CONTEXT = "ProjectContextBlock"
    STYLE_POLICY = "StylePolicyBlock"
    PROMPT_RULES = "PromptRulesBlock"
    CURRENT_SESSION_STATE = "CurrentSessionStateBlock"
    SAFETY_PRIVACY = "SafetyPrivacyBlock"


class DomainModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @field_validator("*")
    @classmethod
    def validate_common_contracts(cls, value: Any, info: ValidationInfo) -> Any:
        field_name = info.field_name
        if value is None or field_name is None:
            return value

        if field_name.endswith("_uuid") and not field_name.endswith("_uuids_ijson"):
            UUID(str(value))

        if _is_timestamp_field(field_name):
            _validate_utc_timestamp(str(value), field_name)

        if field_name.endswith("_ijson") or field_name == "ijson_attachment":
            validate_ijson_text(str(value))

        return value


class Workspace(DomainModel):
    workspace_uuid: str = Field(default_factory=new_uuid)
    name: str = Field(min_length=1)
    description: str | None = None
    created_at: str = Field(default_factory=utc_now)
    updated_at: str | None = None
    schema_version: int = Field(default=1, ge=1)


class Project(DomainModel):
    project_uuid: str = Field(default_factory=new_uuid)
    workspace_uuid: str
    name: str = Field(min_length=1)
    description: str | None = None
    status: str = "active"
    created_at: str = Field(default_factory=utc_now)
    updated_at: str | None = None
    schema_version: int = Field(default=1, ge=1)


class Session(DomainModel):
    session_uuid: str = Field(default_factory=new_uuid)
    workspace_uuid: str | None = None
    project_uuid: str | None = None
    openwebui_conversation_id: str | None = None
    title: str | None = None
    source_app: str = "open_webui"
    created_at: str = Field(default_factory=utc_now)
    updated_at: str | None = None
    conceptual_state_text: str | None = None
    status: SessionStatus = SessionStatus.ACTIVE
    privacy_scope: str = "local"
    active_model_profile_uuid: str | None = None
    content_hash: str | None = None
    schema_version: int = Field(default=1, ge=1)


class SessionEvent(DomainModel):
    event_uuid: str = Field(default_factory=new_uuid)
    session_uuid: str
    event_type: str = Field(min_length=1)
    event_index: int = Field(ge=0)
    payload_ijson: str
    actor_type: str | None = None
    actor_uuid: str | None = None
    created_at: str = Field(default_factory=utc_now)
    content_hash: str | None = None
    schema_version: int = Field(default=1, ge=1)


class Message(DomainModel):
    message_uuid: str = Field(default_factory=new_uuid)
    session_uuid: str
    parent_message_uuid: str | None = None
    turn_index: int | None = None
    role: MessageRole
    creator_type: CreatorType
    model_profile_uuid: str | None = None
    conceptual_linguistic_model_uuid: str | None = None
    raw_text: str | None = None
    raw_payload_ijson: str | None = None
    snapshot_ijson: str | None = None
    content_hash: str | None = None
    token_count_input: int = Field(default=0, ge=0)
    token_count_output: int = Field(default=0, ge=0)
    created_at: str = Field(default_factory=utc_now)
    updated_at: str | None = None
    processing_status: ProcessingStatus = ProcessingStatus.PENDING
    visibility: str = "visible"
    is_deleted: bool = False
    redaction_status: str = "none"
    schema_version: int = Field(default=1, ge=1)


class MessageVersion(DomainModel):
    message_version_uuid: str = Field(default_factory=new_uuid)
    message_uuid: str
    version_number: int = Field(ge=1)
    raw_text: str | None = None
    raw_payload_ijson: str | None = None
    snapshot_ijson: str | None = None
    change_reason: str | None = None
    created_by: str | None = None
    created_at: str = Field(default_factory=utc_now)
    content_hash: str | None = None
    schema_version: int = Field(default=1, ge=1)


class ModelProfile(DomainModel):
    model_profile_uuid: str = Field(default_factory=new_uuid)
    profile_name: str | None = None
    provider: str = Field(min_length=1)
    model_name: str = Field(min_length=1)
    role: ModelRole
    endpoint_url: str | None = None
    is_local: bool = False
    provider_type: str = "unknown"
    provider_name: str | None = None
    endpoint_is_local: bool = False
    context_window: int | None = None
    max_input_tokens: int | None = None
    max_output_tokens: int | None = None
    supports_structured_output: bool = False
    supports_json_mode: bool = False
    supports_embeddings: bool = False
    embedding_dimension: int | None = None
    tokenizer_family: str | None = None
    quality_profile: str = "unknown"
    latency_profile: str = "unknown"
    pricing_ijson: str | None = None
    metadata_ijson: str | None = None
    quality_profile_ijson: str | None = None
    latency_profile_ijson: str | None = None
    cost_profile_ijson: str | None = None
    privacy_profile_ijson: str | None = None
    secret_strategy: str = "none"
    secret_env_var_name: str | None = None
    requires_privacy_acknowledgement: bool = False
    is_enabled: bool = True
    privacy_acknowledged_at: str | None = None
    setup_idempotency_key: str | None = None
    created_at: str = Field(default_factory=utc_now)
    updated_at: str | None = None
    schema_version: int = Field(default=1, ge=1)


class Job(DomainModel):
    job_uuid: str = Field(default_factory=new_uuid)
    job_type: str = Field(min_length=1)
    status: JobStatus = JobStatus.PENDING
    priority: int = 50
    payload_ijson: str
    idempotency_key: str | None = None
    attempts: int = Field(default=0, ge=0)
    max_attempts: int = Field(default=3, ge=1)
    last_error: str | None = None
    last_error_sanitized: str | None = None
    run_after: str | None = None
    locked_by: str | None = None
    locked_at: str | None = None
    lease_token: str | None = None
    lease_generation: int = Field(default=0, ge=0)
    lease_expires_at: str | None = None
    created_at: str = Field(default_factory=utc_now)
    updated_at: str | None = None


class MemoryContextAttachment(DomainModel):
    attachment_uuid: str = Field(default_factory=new_uuid)
    prompt_execution_uuid: str | None = None
    session_uuid: str
    project_uuid: str | None = None
    input_message_uuid: str | None = None
    retrieval_run_uuid: str | None = None
    attachment_mode: AttachmentMode
    ijson_attachment: str
    rendered_attachment: str | None = None
    source_block_uuids_ijson: str | None = None
    source_memory_uuids_ijson: str | None = None
    source_fact_edge_uuids_ijson: str | None = None
    token_count: int = Field(default=0, ge=0)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    abstention_status: str | None = None
    created_at: str = Field(default_factory=utc_now)
    status: str = "active"
    stale_reason: str | None = None
    owner_user_uuid: str | None = None
    workspace_uuid: str | None = None
    lifecycle_status: str = "prepared"
    attachment_review: bool = False
    approved_at: str | None = None
    delivered_at: str | None = None
    suppressed_at: str | None = None
    cancelled_at: str | None = None
    generation: int = Field(default=1, ge=1)
    expires_at: str | None = None
    user_disposition: str = "none"
    user_disposition_at: str | None = None
    schema_version: int = Field(default=1, ge=1)


class MemoryBlock(DomainModel):
    block_uuid: str = Field(default_factory=new_uuid)
    block_type: MemoryBlockType
    scope_type: str = Field(min_length=1)
    scope_uuid: str | None = None
    label: str = Field(min_length=1)
    description: str | None = None
    value: str = Field(min_length=1)
    char_limit: int = Field(gt=0)
    priority: int = 50
    read_only: bool = False
    always_in_context: bool = True
    source_memory_uuids_ijson: str | None = None
    source_nucleus_uuids_ijson: str | None = None
    source_fact_edge_uuids_ijson: str | None = None
    current_version_uuid: str | None = None
    builder_policy_ijson: str | None = None
    optimistic_lock_version: int = Field(default=0, ge=0)
    source_snapshot_hash: str | None = None
    build_status: str = "stale"
    last_compacted_at: str | None = None
    last_used_at: str | None = None
    created_at: str = Field(default_factory=utc_now)
    updated_at: str | None = None
    status: str = "active"
    schema_version: int = Field(default=1, ge=1)


class SessionHotCache(DomainModel):
    session_uuid: str
    current_problem: str | None = None
    last_user_intent: str | None = None
    active_constraints_ijson: str | None = None
    unresolved_questions_ijson: str | None = None
    recent_entities_ijson: str | None = None
    current_topic_nucleus_uuid: str | None = None
    recent_memory_uuids_ijson: str | None = None
    current_plan: str | None = None
    last_updated_at: str = Field(default_factory=utc_now)
    checkpoint_event_uuid: str | None = None
    schema_version: int = Field(default=1, ge=1)


def _is_timestamp_field(field_name: str) -> bool:
    return field_name.endswith(("_at", "_from", "_until", "run_after"))


def _validate_utc_timestamp(value: str, field_name: str) -> None:
    normalized_value = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized_value)
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise ValueError(f"{field_name} must be a timezone-aware UTC timestamp")
