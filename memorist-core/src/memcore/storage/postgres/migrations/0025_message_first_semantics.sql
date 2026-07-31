-- Model-led, message-first semantic ledger. PostgreSQL is canonical in Full.

CREATE TABLE IF NOT EXISTS message_semantic_analyses (
    semantic_analysis_uuid TEXT PRIMARY KEY,
    message_uuid TEXT NOT NULL REFERENCES messages(message_uuid),
    message_version_uuid TEXT REFERENCES message_versions(message_version_uuid),
    processing_run_uuid TEXT NOT NULL REFERENCES memory_processing_runs(processing_run_uuid),
    prompt_execution_uuid TEXT REFERENCES prompt_execution_runs(prompt_execution_uuid),
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
    importance DOUBLE PRECISION NOT NULL DEFAULT 0.5 CHECK(importance >= 0 AND importance <= 1),
    explicit_memory_request BOOLEAN NOT NULL DEFAULT FALSE,
    warnings_jsonb JSONB NOT NULL DEFAULT '[]'::jsonb CHECK(jsonb_typeof(warnings_jsonb) = 'array'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    erased_at TIMESTAMPTZ,
    schema_version INTEGER NOT NULL DEFAULT 1,
    UNIQUE(message_uuid, processing_run_uuid, contract_hash, raw_text_hash)
);
CREATE INDEX IF NOT EXISTS idx_message_semantic_scope
ON message_semantic_analyses(user_uuid, workspace_uuid, project_uuid, session_uuid, status, created_at);

CREATE TABLE IF NOT EXISTS message_semantic_categories (
    semantic_analysis_uuid TEXT NOT NULL REFERENCES message_semantic_analyses(semantic_analysis_uuid) ON DELETE CASCADE,
    category TEXT NOT NULL,
    normalized_label TEXT NOT NULL DEFAULT '',
    confidence DOUBLE PRECISION NOT NULL CHECK(confidence >= 0 AND confidence <= 1),
    PRIMARY KEY(semantic_analysis_uuid, category, normalized_label)
);

CREATE TABLE IF NOT EXISTS canonical_concepts (
    concept_uuid TEXT PRIMARY KEY,
    canonical_label TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    schema_version INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS concept_aliases (
    concept_uuid TEXT NOT NULL REFERENCES canonical_concepts(concept_uuid) ON DELETE CASCADE,
    alias TEXT NOT NULL,
    normalized_alias TEXT NOT NULL UNIQUE,
    language TEXT,
    PRIMARY KEY(concept_uuid, normalized_alias)
);
CREATE TABLE IF NOT EXISTS message_concept_tags (
    semantic_analysis_uuid TEXT NOT NULL REFERENCES message_semantic_analyses(semantic_analysis_uuid) ON DELETE CASCADE,
    concept_uuid TEXT NOT NULL REFERENCES canonical_concepts(concept_uuid),
    tag_ordinal INTEGER NOT NULL CHECK(tag_ordinal BETWEEN 0 AND 4),
    confidence DOUBLE PRECISION NOT NULL CHECK(confidence >= 0 AND confidence <= 1),
    raw_start INTEGER,
    raw_end INTEGER,
    PRIMARY KEY(semantic_analysis_uuid, concept_uuid),
    UNIQUE(semantic_analysis_uuid, tag_ordinal),
    CHECK((raw_start IS NULL AND raw_end IS NULL) OR (raw_start >= 0 AND raw_end > raw_start))
);

CREATE TABLE IF NOT EXISTS message_semantic_units (
    semantic_unit_uuid TEXT PRIMARY KEY,
    semantic_analysis_uuid TEXT NOT NULL REFERENCES message_semantic_analyses(semantic_analysis_uuid) ON DELETE CASCADE,
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
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    erased_at TIMESTAMPTZ,
    schema_version INTEGER NOT NULL DEFAULT 1,
    CHECK(raw_start >= 0 AND raw_end > raw_start),
    UNIQUE(semantic_analysis_uuid, semantic_unit_id),
    UNIQUE(semantic_analysis_uuid, unit_ordinal)
);
CREATE INDEX IF NOT EXISTS idx_message_semantic_units_kind
ON message_semantic_units(memory_kind, lifecycle_status, epistemic_status);

CREATE TABLE IF NOT EXISTS message_entity_references (
    entity_reference_uuid TEXT PRIMARY KEY,
    semantic_analysis_uuid TEXT NOT NULL REFERENCES message_semantic_analyses(semantic_analysis_uuid) ON DELETE CASCADE,
    canonical_name TEXT NOT NULL,
    entity_type TEXT,
    aliases_jsonb JSONB NOT NULL DEFAULT '[]'::jsonb CHECK(jsonb_typeof(aliases_jsonb) = 'array'),
    raw_start INTEGER,
    raw_end INTEGER,
    confidence DOUBLE PRECISION NOT NULL CHECK(confidence >= 0 AND confidence <= 1)
);
CREATE TABLE IF NOT EXISTS message_process_references (
    process_reference_uuid TEXT PRIMARY KEY,
    semantic_analysis_uuid TEXT NOT NULL REFERENCES message_semantic_analyses(semantic_analysis_uuid) ON DELETE CASCADE,
    process_label TEXT NOT NULL,
    process_aliases_jsonb JSONB NOT NULL DEFAULT '[]'::jsonb CHECK(jsonb_typeof(process_aliases_jsonb) = 'array'),
    stage_label TEXT,
    stage_ordinal INTEGER,
    raw_start INTEGER,
    raw_end INTEGER,
    confidence DOUBLE PRECISION NOT NULL CHECK(confidence >= 0 AND confidence <= 1)
);

CREATE TABLE IF NOT EXISTS semantic_job_outcomes (
    semantic_job_outcome_uuid TEXT PRIMARY KEY,
    semantic_analysis_uuid TEXT REFERENCES message_semantic_analyses(semantic_analysis_uuid),
    message_uuid TEXT NOT NULL REFERENCES messages(message_uuid),
    processing_run_uuid TEXT NOT NULL REFERENCES memory_processing_runs(processing_run_uuid),
    job_uuid TEXT,
    outcome TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    called_provider BOOLEAN NOT NULL,
    provider_output_valid BOOLEAN NOT NULL,
    fallback_used BOOLEAN NOT NULL,
    candidate_count INTEGER NOT NULL DEFAULT 0,
    memory_count INTEGER NOT NULL DEFAULT 0,
    latency_ms INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    schema_version INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS model_retrieval_plans (
    retrieval_plan_uuid TEXT PRIMARY KEY,
    retrieval_run_uuid TEXT NOT NULL UNIQUE,
    input_message_uuid TEXT NOT NULL REFERENCES messages(message_uuid),
    user_uuid TEXT NOT NULL,
    workspace_uuid TEXT,
    project_uuid TEXT,
    intent TEXT,
    primary_topic TEXT,
    secondary_topic TEXT,
    entities_jsonb JSONB NOT NULL DEFAULT '[]'::jsonb CHECK(jsonb_typeof(entities_jsonb) = 'array'),
    process_label TEXT,
    stage_ordinal INTEGER,
    requested_operation TEXT,
    requested_time TEXT,
    expected_answer_type TEXT,
    relation_hints_jsonb JSONB NOT NULL DEFAULT '[]'::jsonb CHECK(jsonb_typeof(relation_hints_jsonb) = 'array'),
    contract_hash TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    schema_version INTEGER NOT NULL DEFAULT 1
);
