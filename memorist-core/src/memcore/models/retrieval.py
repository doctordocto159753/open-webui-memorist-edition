from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from memcore.models.domain import DomainModel, new_uuid, utc_now


class RetrievalMode(StrEnum):
    LITE = "lite"
    STANDARD = "standard"
    FULL = "full"
    DEBUG = "debug"


class RetrievalRunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    ABSTAINED = "abstained"
    FAILED = "failed"


class QueryIntent(StrEnum):
    CURRENT_TASK = "current_task"
    USER_PROFILE = "user_profile"
    PROJECT_CONTEXT = "project_context"
    PAST_DECISION = "past_decision"
    CONSTRAINT = "constraint"
    PREFERENCE = "preference"
    DEFINITION = "definition"
    PAST_EPISODE = "past_episode"
    PROCEDURE = "procedure"
    TEMPORAL_QUESTION = "temporal_question"
    CORRECTION_OR_UPDATE = "correction_or_update"


class RetrievalScopeType(StrEnum):
    SESSION = "session"
    PROJECT = "project"
    WORKSPACE = "workspace"
    USER_LOCAL = "user-local"


class RetrievalQueryType(StrEnum):
    ORIGINAL = "original"
    EXPANDED = "expanded"
    ENTITY = "entity"
    TEMPORAL = "temporal"
    CANONICAL_KEY = "canonical_key"


class RetrievalCandidateDecision(StrEnum):
    CANDIDATE = "candidate"
    SELECTED = "selected"
    EXCLUDED = "excluded"
    CONFLICT_BUNDLE = "conflict_bundle"


class AttachmentMode(StrEnum):
    LITE = "lite"
    STANDARD = "standard"
    FULL = "full"


class PreflightStatus(StrEnum):
    ATTACHED = "attached"
    ABSTAINED = "abstained"
    DISABLED = "disabled"
    TIMEOUT = "timeout"
    FAILED_OPEN = "failed_open"
    DEGRADED_LITE = "degraded_lite"
    SKIPPED_BACKPRESSURE = "skipped_backpressure"


class TemporalFilter(BaseModel):
    phrase: str
    normalized_from: str | None = None
    normalized_until: str | None = None
    precision: str | None = None
    historical: bool = False


class ExpandedQuery(BaseModel):
    query_text: str
    query_type: RetrievalQueryType = RetrievalQueryType.EXPANDED
    generated: bool = True
    weight: float = Field(default=1.0, ge=0.0)


class RetrievalScope(BaseModel):
    scope_type: RetrievalScopeType
    scope_uuid: str | None = None


class RetrievalPlan(BaseModel):
    original_query: str
    retrieval_mode: RetrievalMode
    query_intents: list[QueryIntent]
    expanded_queries: list[ExpandedQuery]
    scopes: list[RetrievalScope]
    memory_types: list[str]
    focal_entities: list[str]
    temporal_filter: TemporalFilter | None
    include_conflicts: bool
    token_budget: int = Field(ge=1)
    abstain_if_insufficient: bool


class RetrievalRun(DomainModel):
    retrieval_run_uuid: str = Field(default_factory=new_uuid)
    session_uuid: str
    project_uuid: str | None = None
    input_message_uuid: str
    retrieval_mode: RetrievalMode
    original_query: str
    token_budget: int = Field(ge=1)
    status: RetrievalRunStatus = RetrievalRunStatus.PENDING
    planner_version: str
    config_snapshot_ijson: str
    abstention_status: str | None = None
    abstention_reason: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    latency_ms: int | None = Field(default=None, ge=0)
    created_at: str = Field(default_factory=utc_now)
    schema_version: int = Field(default=1, ge=1)


class RetrievalQuery(DomainModel):
    retrieval_query_uuid: str = Field(default_factory=new_uuid)
    retrieval_run_uuid: str
    query_index: int = Field(ge=0)
    query_type: RetrievalQueryType
    query_text: str
    weight: float = Field(default=1.0, ge=0.0)
    filters_ijson: str
    created_at: str = Field(default_factory=utc_now)
    schema_version: int = Field(default=1, ge=1)


class RetrievalCandidate(DomainModel):
    retrieval_candidate_uuid: str = Field(default_factory=new_uuid)
    retrieval_run_uuid: str
    retrieval_query_uuid: str | None = None
    memory_uuid: str
    memory_version_uuid: str
    generator_type: str
    generator_rank: int | None = Field(default=None, ge=0)
    lexical_score: float | None = None
    semantic_score: float | None = None
    graph_score: float | None = None
    temporal_score: float | None = None
    authority_score: float | None = None
    confidence_score: float | None = None
    conflict_penalty: float = 0.0
    fused_score: float | None = None
    rerank_score: float | None = None
    final_score: float | None = None
    decision: RetrievalCandidateDecision = RetrievalCandidateDecision.CANDIDATE
    exclusion_reasons_ijson: str | None = None
    score_trace_ijson: str | None = None
    created_at: str = Field(default_factory=utc_now)
    schema_version: int = Field(default=1, ge=1)


class MemoryVersionEmbedding(DomainModel):
    memory_version_uuid: str
    embedding_model: str
    embedding_dimension: int = Field(gt=0)
    embedding_version: str
    content_hash: str
    embedding_ijson: str
    created_at: str = Field(default_factory=utc_now)
    schema_version: int = Field(default=1, ge=1)


class PreflightResponse(BaseModel):
    status: PreflightStatus
    attachment_uuid: str | None = None
    retrieval_run_uuid: str | None = None
    rendered_attachment: str | None = None
    token_count: int = 0
    abstention_status: str | None = None
    latency_ms: int
    effective_token_budget: int = 0
    budget_reason: str | None = None
    token_estimation_method: str | None = None
    attachment_mode: str | None = None
    budget_warnings: list[str] = Field(default_factory=list)


class ScoredMemoryItem(BaseModel):
    memory_uuid: str
    memory_version_uuid: str
    memory_type: str
    scope_type: str
    scope_uuid: str | None = None
    normalized_text: str
    current: bool
    valid_time_label: str
    confidence_label: str
    source_authority_label: str
    evidence_text: str | None = None
    conflict_status: str = "none"
    final_score: float
    debug_score_trace: dict[str, Any] = Field(default_factory=dict)
