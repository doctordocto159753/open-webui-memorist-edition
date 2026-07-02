from enum import StrEnum

from pydantic import Field, model_validator

from memcore.models.domain import DomainModel, new_uuid, utc_now


class ProcessingRunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class TextUnitType(StrEnum):
    SENTENCE = "sentence"
    LIST_ITEM = "list_item"
    PARAGRAPH = "paragraph"
    CODE_BLOCK = "code_block"
    QUOTE = "quote"
    HEADING = "heading"
    FRAGMENT = "fragment"


class GateDecisionValue(StrEnum):
    DISCARD = "discard"
    RETAIN_RAW_ONLY = "retain_raw_only"
    ANALYZE = "analyze"
    ANALYZE_HIGH_CONFIDENCE = "analyze_high_confidence"
    MANUAL_REVIEW = "manual_review"


class CandidateType(StrEnum):
    FACT = "fact"
    PREFERENCE = "preference"
    GOAL = "goal"
    CONSTRAINT = "constraint"
    DECISION = "decision"
    INSTRUCTION = "instruction"
    DEFINITION = "definition"
    EPISODE = "episode"
    FEEDBACK = "feedback"
    RELATIONSHIP = "relationship"
    IDENTITY = "identity"
    STYLE = "style"
    UNRESOLVED_QUESTION = "unresolved_question"


class SourceAuthority(StrEnum):
    USER_EXPLICIT = "user_explicit"
    USER_IMPLIED = "user_implied"
    ASSISTANT_CLAIM = "assistant_claim"
    TOOL_OBSERVATION = "tool_observation"
    SYSTEM_INSTRUCTION = "system_instruction"
    IMPORTED_RECORD = "imported_record"


class Explicitness(StrEnum):
    EXPLICIT = "explicit"
    IMPLIED = "implied"
    INFERRED = "inferred"


class CandidateStatus(StrEnum):
    PENDING = "pending"
    VALIDATED = "validated"
    REJECTED = "rejected"
    NEEDS_REVIEW = "needs_review"
    READY_FOR_CONSOLIDATION = "ready_for_consolidation"


class SensitivityClass(StrEnum):
    NORMAL = "normal"
    SENSITIVE = "sensitive"
    SECRET = "secret"


class EvidenceRole(StrEnum):
    PRIMARY = "primary"
    CONTEXT = "context"


class SupportType(StrEnum):
    SUPPORTING = "supporting"
    CONTRADICTING = "contradicting"
    CONTEXTUAL = "contextual"


class MemoryStatus(StrEnum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    RETRACTED = "retracted"
    FORGOTTEN = "forgotten"
    NEEDS_REVIEW = "needs_review"


class ChangeOperation(StrEnum):
    ADD = "add"
    REINFORCE = "reinforce"
    UPDATE = "update"
    SUPERSEDE = "supersede"
    CONTRADICT = "contradict"
    RETRACT = "retract"
    FORGET = "forget"


class ConsolidationOperation(StrEnum):
    ADD = "ADD"
    REINFORCE = "REINFORCE"
    UPDATE = "UPDATE"
    SUPERSEDE = "SUPERSEDE"
    CONTRADICT = "CONTRADICT"
    RETRACT = "RETRACT"
    FORGET = "FORGET"
    NOOP = "NOOP"
    REJECT = "REJECT"
    MANUAL_REVIEW = "MANUAL_REVIEW"


class OutboxStatus(StrEnum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    RETRY = "retry"
    FAILED = "failed"
    SKIPPED = "skipped"


class MemoryProcessingRun(DomainModel):
    processing_run_uuid: str = Field(default_factory=new_uuid)
    session_uuid: str
    message_uuid: str
    pipeline_version: str
    prompt_bundle_version: str | None = None
    prompt_id: str | None = None
    prompt_version: str | None = None
    model_profile_uuid: str | None = None
    provider_type: str | None = None
    input_content_hash: str
    input_hash: str | None = None
    output_hash: str | None = None
    status: ProcessingRunStatus = ProcessingRunStatus.PENDING
    started_at: str | None = None
    completed_at: str | None = None
    latency_ms: int | None = Field(default=None, ge=0)
    token_count_input: int = Field(default=0, ge=0)
    token_count_output: int = Field(default=0, ge=0)
    raw_output_ijson: str | None = None
    validation_errors_ijson: str | None = None
    error_text: str | None = None
    created_at: str = Field(default_factory=utc_now)
    schema_version: int = Field(default=1, ge=1)


class TextUnit(DomainModel):
    text_unit_uuid: str = Field(default_factory=new_uuid)
    unit_uuid: str | None = None
    message_uuid: str
    session_uuid: str
    unit_index: int = Field(ge=0)
    unit_type: TextUnitType
    text: str = Field(min_length=1)
    start_char: int = Field(ge=0)
    end_char: int = Field(gt=0)
    char_start: int | None = Field(default=None, ge=0)
    char_end: int | None = Field(default=None, gt=0)
    language_code: str | None = None
    language_hint: str | None = None
    speaker_role: str
    content_hash: str
    segmentation_confidence: str | None = None
    segmentation_notes: str | None = None
    created_at: str = Field(default_factory=utc_now)
    schema_version: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def validate_span(self) -> "TextUnit":
        if self.unit_uuid is None:
            self.unit_uuid = self.text_unit_uuid
        if self.char_start is None:
            self.char_start = self.start_char
        if self.char_end is None:
            self.char_end = self.end_char
        if self.language_hint is None:
            self.language_hint = self.language_code
        if self.segmentation_confidence is None:
            self.segmentation_confidence = "medium"
        if self.end_char <= self.start_char:
            raise ValueError("end_char must be greater than start_char")
        if self.end_char - self.start_char != len(self.text):
            raise ValueError("text length must match start_char/end_char span length")
        if self.char_start != self.start_char or self.char_end != self.end_char:
            raise ValueError("char_start/char_end must mirror start_char/end_char")
        return self


class MemoryGateDecision(DomainModel):
    gate_decision_uuid: str = Field(default_factory=new_uuid)
    text_unit_uuid: str
    processing_run_uuid: str
    decision: GateDecisionValue
    reason_codes_ijson: str
    salience_score: float = Field(ge=0.0, le=1.0)
    persistence_score: float = Field(ge=0.0, le=1.0)
    actionability_score: float = Field(ge=0.0, le=1.0)
    sensitivity_score: float = Field(ge=0.0, le=1.0)
    novelty_score: float = Field(ge=0.0, le=1.0)
    requires_high_confidence_pass: bool = False
    created_at: str = Field(default_factory=utc_now)
    schema_version: int = Field(default=1, ge=1)


class LinguisticAnalysis(DomainModel):
    analysis_uuid: str = Field(default_factory=new_uuid)
    text_unit_uuid: str
    processing_run_uuid: str
    analysis_schema_version: str
    speech_acts_ijson: str
    jakobson_functions_ijson: str
    conceptual_nuclei_ijson: str
    entity_mentions_ijson: str
    temporal_expressions_ijson: str
    modality_ijson: str
    memory_signals_ijson: str
    abstention_ijson: str
    raw_output_ijson: str | None = None
    validation_errors_ijson: str | None = None
    created_at: str = Field(default_factory=utc_now)
    schema_version: int = Field(default=1, ge=1)


class MemoryCandidate(DomainModel):
    candidate_uuid: str = Field(default_factory=new_uuid)
    processing_run_uuid: str
    text_unit_uuid: str
    prompt_execution_uuid: str | None = None
    candidate_type: CandidateType
    subject_key: str | None = None
    predicate: str | None = None
    object_ijson: str | None = None
    normalized_text: str
    source_authority: SourceAuthority
    explicitness: Explicitness
    confidence: float = Field(ge=0.0, le=1.0)
    importance: float = Field(ge=0.0, le=1.0)
    valid_from: str | None = None
    valid_until: str | None = None
    temporal_precision: str | None = None
    status: CandidateStatus = CandidateStatus.PENDING
    sensitivity_class: SensitivityClass = SensitivityClass.NORMAL
    extraction_metadata_ijson: str | None = None
    rejection_reason_codes_ijson: str | None = None
    created_at: str = Field(default_factory=utc_now)
    schema_version: int = Field(default=1, ge=1)


class CandidateEvidence(DomainModel):
    evidence_uuid: str = Field(default_factory=new_uuid)
    candidate_uuid: str
    message_uuid: str
    text_unit_uuid: str
    annotation_uuid: str | None = None
    route_uuid: str | None = None
    evidence_text: str = Field(min_length=1)
    start_char: int = Field(ge=0)
    end_char: int = Field(gt=0)
    evidence_role: EvidenceRole
    support_type: SupportType
    created_at: str = Field(default_factory=utc_now)
    schema_version: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def validate_span(self) -> "CandidateEvidence":
        if self.end_char <= self.start_char:
            raise ValueError("end_char must be greater than start_char")
        if self.end_char - self.start_char != len(self.evidence_text):
            raise ValueError("evidence_text length must match start_char/end_char span length")
        return self


class JakobsonFunction(StrEnum):
    REFERENTIAL = "referential"
    EMOTIVE = "emotive"
    CONATIVE = "conative"
    PHATIC = "phatic"
    METALINGUAL = "metalingual"
    POETIC = "poetic"


class JakobsonConfidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class JakobsonAnalysisRunStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    MANUAL_REVIEW = "manual_review"


class MemorySignalRouteStatus(StrEnum):
    PENDING = "pending"
    READY = "ready"
    IGNORED = "ignored"
    FAILED = "failed"


class MemorySignalRouteType(StrEnum):
    IGNORE = "ignore"
    PROJECT_CONTEXT = "project_context"
    WORKFLOW_POLICY = "workflow_policy"
    TEAM_OBLIGATION = "team_obligation"
    PROMPT_INSTRUCTION = "prompt_instruction"
    STYLE_POLICY = "style_policy"
    TERMINOLOGY_RULE = "terminology_rule"
    USER_PREFERENCE = "user_preference"
    EMOTIONAL_STANCE = "emotional_stance"
    PROCESS_FACT = "process_fact"
    JIRA_CONFIGURATION = "jira_configuration"
    TASK_CONSTRAINT = "task_constraint"
    RESOURCE_REFERENCE = "resource_reference"
    PRIVACY_REVIEW = "privacy_review"
    MANUAL_REVIEW = "manual_review"


class JakobsonAnalysisRun(DomainModel):
    analysis_run_uuid: str = Field(default_factory=new_uuid)
    prompt_execution_uuid: str | None = None
    workspace_uuid: str | None = None
    project_uuid: str | None = None
    session_uuid: str
    message_uuid: str
    prompt_id: str
    prompt_version: str
    model_profile_uuid: str | None = None
    provider_type: str
    model_name: str | None = None
    input_hash: str
    output_hash: str | None = None
    status: JakobsonAnalysisRunStatus = JakobsonAnalysisRunStatus.SUCCEEDED
    warnings_ijson: str
    raw_output_ijson: str | None = None
    created_at: str = Field(default_factory=utc_now)
    schema_version: int = Field(default=1, ge=1)


class JakobsonSentenceAnnotation(DomainModel):
    annotation_uuid: str = Field(default_factory=new_uuid)
    analysis_run_uuid: str
    message_uuid: str
    unit_uuid: str
    sentence_index: int = Field(ge=1)
    sentence_text: str = Field(min_length=1)
    sentence_hash: str
    sender_value: str | None = None
    sender_evidence: str | None = None
    sender_confidence: JakobsonConfidence
    receiver_value: str | None = None
    receiver_evidence: str | None = None
    receiver_confidence: JakobsonConfidence
    message_value: str | None = None
    message_evidence: str | None = None
    message_confidence: JakobsonConfidence
    context_value: str | None = None
    context_evidence: str | None = None
    context_confidence: JakobsonConfidence
    code_value: str | None = None
    code_evidence: str | None = None
    code_confidence: JakobsonConfidence
    contact_channel_value: str | None = None
    contact_channel_evidence: str | None = None
    contact_channel_confidence: JakobsonConfidence
    dominant_function: JakobsonFunction
    secondary_functions_ijson: str
    function_reason: str | None = None
    notes: str | None = None
    raw_sentence_output_ijson: str
    created_at: str = Field(default_factory=utc_now)
    schema_version: int = Field(default=1, ge=1)


class MemorySignalRoute(DomainModel):
    route_uuid: str = Field(default_factory=new_uuid)
    prompt_execution_uuid: str | None = None
    annotation_uuid: str
    message_uuid: str
    unit_uuid: str
    dominant_function: JakobsonFunction
    secondary_functions_ijson: str
    route_type: MemorySignalRouteType
    extractor_id: str
    priority: int = Field(ge=0, le=100)
    confidence: JakobsonConfidence
    reason: str
    status: MemorySignalRouteStatus = MemorySignalRouteStatus.READY
    created_at: str = Field(default_factory=utc_now)
    schema_version: int = Field(default=1, ge=1)


class Memory(DomainModel):
    memory_uuid: str = Field(default_factory=new_uuid)
    canonical_key: str
    memory_type: str
    scope_type: str
    scope_uuid: str | None = None
    current_version_uuid: str | None = None
    status: MemoryStatus = MemoryStatus.ACTIVE
    sensitivity_class: SensitivityClass = SensitivityClass.NORMAL
    created_at: str = Field(default_factory=utc_now)
    updated_at: str | None = None
    schema_version: int = Field(default=1, ge=1)


class MemoryVersion(DomainModel):
    memory_version_uuid: str = Field(default_factory=new_uuid)
    memory_uuid: str
    prompt_execution_uuid: str | None = None
    version_number: int = Field(ge=1)
    normalized_text: str
    value_ijson: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    importance: float = Field(ge=0.0, le=1.0)
    valid_from: str | None = None
    valid_until: str | None = None
    transaction_from: str = Field(default_factory=utc_now)
    transaction_until: str | None = None
    source_candidate_uuid: str | None = None
    change_operation: ChangeOperation
    change_reason_ijson: str | None = None
    created_at: str = Field(default_factory=utc_now)
    schema_version: int = Field(default=1, ge=1)


class MemoryEvidenceLink(DomainModel):
    memory_evidence_link_uuid: str = Field(default_factory=new_uuid)
    memory_uuid: str
    memory_version_uuid: str
    candidate_uuid: str
    evidence_uuid: str
    link_type: str = "supports"
    created_at: str = Field(default_factory=utc_now)
    schema_version: int = Field(default=1, ge=1)


class MemoryRelation(DomainModel):
    relation_uuid: str = Field(default_factory=new_uuid)
    source_memory_uuid: str
    target_memory_uuid: str
    relation_type: str
    source_memory_version_uuid: str | None = None
    target_memory_version_uuid: str | None = None
    created_at: str = Field(default_factory=utc_now)
    schema_version: int = Field(default=1, ge=1)


class MemoryConsolidationDecision(DomainModel):
    consolidation_decision_uuid: str = Field(default_factory=new_uuid)
    candidate_uuid: str
    operation: ConsolidationOperation
    resolver_version: str
    reason_codes_ijson: str
    target_memory_uuid: str | None = None
    target_memory_version_uuid: str | None = None
    created_at: str = Field(default_factory=utc_now)
    schema_version: int = Field(default=1, ge=1)


class GraphProjectionOutboxItem(DomainModel):
    outbox_uuid: str = Field(default_factory=new_uuid)
    aggregate_type: str
    aggregate_uuid: str
    operation: str
    payload_ijson: str
    status: OutboxStatus = OutboxStatus.PENDING
    attempts: int = Field(default=0, ge=0)
    last_error: str | None = None
    created_at: str = Field(default_factory=utc_now)
    processed_at: str | None = None
