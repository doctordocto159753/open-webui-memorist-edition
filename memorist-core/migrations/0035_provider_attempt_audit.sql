-- Additive provider-attempt audit and final stage authority (schema 24).
-- A row is reserved and committed before each remote call. Until finalized it
-- remains unknown_completion, preserving truthful crash-window evidence.
CREATE TABLE IF NOT EXISTS processing_provider_attempts (
    provider_attempt_uuid TEXT PRIMARY KEY,
    stage_execution_uuid TEXT NOT NULL,
    processing_run_uuid TEXT,
    job_uuid TEXT,
    source_type TEXT NOT NULL,
    source_uuid TEXT NOT NULL,
    attempt_index INTEGER NOT NULL,
    attempt_kind TEXT NOT NULL,
    requested_role TEXT NOT NULL,
    effective_role TEXT NOT NULL,
    model_profile_uuid TEXT,
    profile_fingerprint TEXT NOT NULL,
    scope_source TEXT NOT NULL,
    inheritance_source TEXT,
    provider_type TEXT NOT NULL,
    model_name TEXT NOT NULL,
    capability_mode TEXT NOT NULL,
    prompt_id TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    contract_hash TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    response_output_hash TEXT,
    provider_response_id TEXT,
    http_status INTEGER,
    transport_status TEXT NOT NULL DEFAULT 'unknown_completion',
    parse_status TEXT NOT NULL DEFAULT 'not_observed',
    schema_valid INTEGER,
    canonicalized INTEGER NOT NULL DEFAULT 0,
    validation_errors_ijson TEXT NOT NULL DEFAULT '[]',
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    latency_ms INTEGER NOT NULL DEFAULT 0,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    idempotency_identity TEXT NOT NULL UNIQUE,
    schema_version INTEGER NOT NULL DEFAULT 1,
    UNIQUE(stage_execution_uuid, attempt_index),
    FOREIGN KEY(processing_run_uuid) REFERENCES memory_processing_runs(processing_run_uuid),
    FOREIGN KEY(model_profile_uuid) REFERENCES model_profiles(model_profile_uuid)
);
CREATE INDEX IF NOT EXISTS idx_provider_attempts_stage
ON processing_provider_attempts(stage_execution_uuid, attempt_index);
CREATE INDEX IF NOT EXISTS idx_provider_attempts_source
ON processing_provider_attempts(source_type, source_uuid, started_at);

ALTER TABLE processing_stage_runs ADD COLUMN job_uuid TEXT;
ALTER TABLE processing_stage_runs ADD COLUMN contract_hash TEXT;
ALTER TABLE processing_stage_runs ADD COLUMN profile_fingerprint TEXT;
ALTER TABLE processing_stage_runs ADD COLUMN canonicalized INTEGER NOT NULL DEFAULT 0;
ALTER TABLE processing_stage_runs ADD COLUMN attempt_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE processing_stage_runs ADD COLUMN total_provider_latency_ms INTEGER NOT NULL DEFAULT 0;
