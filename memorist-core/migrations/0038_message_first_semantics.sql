-- Model-led, message-first semantic ledger. Legacy Jakobson/gate/route rows remain audit data.

CREATE TABLE message_semantic_analyses (
    semantic_analysis_uuid TEXT PRIMARY KEY,
    message_uuid TEXT NOT NULL,
    message_version_uuid TEXT,
    processing_run_uuid TEXT NOT NULL,
    prompt_execution_uuid TEXT,
    stage_execution_uuid TEXT,
    workspace_uuid TEXT,
    project_uuid TEXT,
    session_uuid TEXT NOT NULL,
    user_uuid TEXT,
    source_role TEXT NOT NULL,
    source_authority TEXT NOT NULL,
    contract_hash TEXT NOT NULL,
    raw_text_hash TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('succeeded', 'partial', 'abstained', 'blocked', 'failed_open', 'erased')),
    semantic_outcome TEXT NOT NULL,
    summary_intent TEXT,
    primary_topic TEXT,
    secondary_topic TEXT,
    one_line_summary TEXT,
    epistemic_status TEXT NOT NULL DEFAULT 'unknown',
    temporal_status TEXT NOT NULL DEFAULT 'unknown',
    importance REAL NOT NULL DEFAULT 0.5 CHECK(importance >= 0 AND importance <= 1),
    explicit_memory_request INTEGER NOT NULL DEFAULT 0 CHECK(explicit_memory_request IN (0, 1)),
    warnings_ijson TEXT NOT NULL DEFAULT '[]' CHECK(json_valid(warnings_ijson)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    erased_at TEXT,
    schema_version INTEGER NOT NULL DEFAULT 1,
    UNIQUE(message_uuid, processing_run_uuid, contract_hash, raw_text_hash),
    FOREIGN KEY(message_uuid) REFERENCES messages(message_uuid),
    FOREIGN KEY(message_version_uuid) REFERENCES message_versions(message_version_uuid),
    FOREIGN KEY(processing_run_uuid) REFERENCES memory_processing_runs(processing_run_uuid),
    FOREIGN KEY(prompt_execution_uuid) REFERENCES prompt_execution_runs(prompt_execution_uuid)
);

CREATE INDEX idx_message_semantic_scope
ON message_semantic_analyses(user_uuid, workspace_uuid, project_uuid, session_uuid, status, created_at);

CREATE TABLE message_semantic_categories (
    semantic_analysis_uuid TEXT NOT NULL,
    category TEXT NOT NULL,
    normalized_label TEXT,
    confidence REAL NOT NULL CHECK(confidence >= 0 AND confidence <= 1),
    PRIMARY KEY(semantic_analysis_uuid, category, normalized_label),
    FOREIGN KEY(semantic_analysis_uuid) REFERENCES message_semantic_analyses(semantic_analysis_uuid) ON DELETE CASCADE
);

CREATE TABLE canonical_concepts (
    concept_uuid TEXT PRIMARY KEY,
    canonical_label TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE concept_aliases (
    concept_uuid TEXT NOT NULL,
    alias TEXT NOT NULL,
    normalized_alias TEXT NOT NULL UNIQUE,
    language TEXT,
    PRIMARY KEY(concept_uuid, normalized_alias),
    FOREIGN KEY(concept_uuid) REFERENCES canonical_concepts(concept_uuid) ON DELETE CASCADE
);

CREATE TABLE message_concept_tags (
    semantic_analysis_uuid TEXT NOT NULL,
    concept_uuid TEXT NOT NULL,
    tag_ordinal INTEGER NOT NULL CHECK(tag_ordinal BETWEEN 0 AND 4),
    confidence REAL NOT NULL CHECK(confidence >= 0 AND confidence <= 1),
    raw_start INTEGER,
    raw_end INTEGER,
    PRIMARY KEY(semantic_analysis_uuid, concept_uuid),
    UNIQUE(semantic_analysis_uuid, tag_ordinal),
    CHECK((raw_start IS NULL AND raw_end IS NULL) OR (raw_start >= 0 AND raw_end > raw_start)),
    FOREIGN KEY(semantic_analysis_uuid) REFERENCES message_semantic_analyses(semantic_analysis_uuid) ON DELETE CASCADE,
    FOREIGN KEY(concept_uuid) REFERENCES canonical_concepts(concept_uuid)
);

CREATE TABLE message_semantic_units (
    semantic_unit_uuid TEXT PRIMARY KEY,
    semantic_analysis_uuid TEXT NOT NULL,
    semantic_unit_id TEXT NOT NULL,
    unit_ordinal INTEGER NOT NULL,
    raw_start INTEGER NOT NULL,
    raw_end INTEGER NOT NULL,
    evidence_hash TEXT NOT NULL,
    proposition_text TEXT,
    unit_type TEXT NOT NULL,
    memory_kind TEXT,
    durability TEXT NOT NULL,
    polarity TEXT NOT NULL,
    epistemic_status TEXT NOT NULL,
    lifecycle_status TEXT NOT NULL DEFAULT 'unknown',
    created_at TEXT NOT NULL,
    erased_at TEXT,
    schema_version INTEGER NOT NULL DEFAULT 1,
    CHECK(raw_start >= 0 AND raw_end > raw_start),
    UNIQUE(semantic_analysis_uuid, semantic_unit_id),
    UNIQUE(semantic_analysis_uuid, unit_ordinal),
    FOREIGN KEY(semantic_analysis_uuid) REFERENCES message_semantic_analyses(semantic_analysis_uuid) ON DELETE CASCADE
);

CREATE INDEX idx_message_semantic_units_kind
ON message_semantic_units(memory_kind, lifecycle_status, epistemic_status);

CREATE TABLE message_entity_references (
    entity_reference_uuid TEXT PRIMARY KEY,
    semantic_analysis_uuid TEXT NOT NULL,
    canonical_name TEXT NOT NULL,
    entity_type TEXT,
    aliases_ijson TEXT NOT NULL DEFAULT '[]' CHECK(json_valid(aliases_ijson)),
    raw_start INTEGER,
    raw_end INTEGER,
    confidence REAL NOT NULL CHECK(confidence >= 0 AND confidence <= 1),
    FOREIGN KEY(semantic_analysis_uuid) REFERENCES message_semantic_analyses(semantic_analysis_uuid) ON DELETE CASCADE
);

CREATE TABLE message_process_references (
    process_reference_uuid TEXT PRIMARY KEY,
    semantic_analysis_uuid TEXT NOT NULL,
    process_label TEXT NOT NULL,
    process_aliases_ijson TEXT NOT NULL DEFAULT '[]' CHECK(json_valid(process_aliases_ijson)),
    stage_label TEXT,
    stage_ordinal INTEGER,
    raw_start INTEGER,
    raw_end INTEGER,
    confidence REAL NOT NULL CHECK(confidence >= 0 AND confidence <= 1),
    FOREIGN KEY(semantic_analysis_uuid) REFERENCES message_semantic_analyses(semantic_analysis_uuid) ON DELETE CASCADE
);

CREATE TABLE semantic_job_outcomes (
    semantic_job_outcome_uuid TEXT PRIMARY KEY,
    semantic_analysis_uuid TEXT,
    message_uuid TEXT NOT NULL,
    processing_run_uuid TEXT NOT NULL,
    job_uuid TEXT,
    outcome TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    called_provider INTEGER NOT NULL CHECK(called_provider IN (0, 1)),
    provider_output_valid INTEGER NOT NULL CHECK(provider_output_valid IN (0, 1)),
    fallback_used INTEGER NOT NULL CHECK(fallback_used IN (0, 1)),
    candidate_count INTEGER NOT NULL DEFAULT 0,
    memory_count INTEGER NOT NULL DEFAULT 0,
    latency_ms INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY(semantic_analysis_uuid) REFERENCES message_semantic_analyses(semantic_analysis_uuid),
    FOREIGN KEY(message_uuid) REFERENCES messages(message_uuid),
    FOREIGN KEY(processing_run_uuid) REFERENCES memory_processing_runs(processing_run_uuid)
);

CREATE TABLE model_retrieval_plans (
    retrieval_plan_uuid TEXT PRIMARY KEY,
    retrieval_run_uuid TEXT NOT NULL,
    input_message_uuid TEXT NOT NULL,
    user_uuid TEXT NOT NULL,
    workspace_uuid TEXT,
    project_uuid TEXT,
    intent TEXT,
    primary_topic TEXT,
    secondary_topic TEXT,
    entities_ijson TEXT NOT NULL DEFAULT '[]' CHECK(json_valid(entities_ijson)),
    process_label TEXT,
    stage_ordinal INTEGER,
    requested_operation TEXT,
    requested_time TEXT,
    expected_answer_type TEXT,
    relation_hints_ijson TEXT NOT NULL DEFAULT '[]' CHECK(json_valid(relation_hints_ijson)),
    contract_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1,
    UNIQUE(retrieval_run_uuid),
    FOREIGN KEY(input_message_uuid) REFERENCES messages(message_uuid)
);
