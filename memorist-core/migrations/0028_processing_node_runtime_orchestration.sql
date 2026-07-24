ALTER TABLE model_profiles ADD COLUMN setup_idempotency_key TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS uq_model_profiles_setup_idempotency
ON model_profiles(setup_idempotency_key)
WHERE setup_idempotency_key IS NOT NULL;

ALTER TABLE model_health_events ADD COLUMN result_ijson TEXT;
ALTER TABLE model_health_events ADD COLUMN test_idempotency_key TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS uq_model_health_test_idempotency
ON model_health_events(model_profile_uuid, test_idempotency_key)
WHERE test_idempotency_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS processing_stage_runs (
    stage_execution_uuid TEXT PRIMARY KEY,
    processing_run_uuid TEXT,
    source_type TEXT NOT NULL,
    source_uuid TEXT NOT NULL,
    requested_role TEXT NOT NULL,
    effective_role TEXT NOT NULL,
    stage TEXT NOT NULL,
    model_profile_uuid TEXT,
    provider_type TEXT NOT NULL,
    model_name TEXT NOT NULL,
    prompt_id TEXT,
    prompt_version TEXT,
    input_hash TEXT NOT NULL,
    output_hash TEXT,
    status TEXT NOT NULL,
    called_provider INTEGER NOT NULL DEFAULT 0,
    fallback_used INTEGER NOT NULL DEFAULT 0,
    scope_source TEXT NOT NULL,
    inheritance_source TEXT,
    fallback_reason TEXT,
    detail_sanitized TEXT,
    validation_errors_ijson TEXT,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    embedding_count INTEGER NOT NULL DEFAULT 0,
    latency_ms INTEGER NOT NULL DEFAULT 0,
    idempotency_key TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    completed_at TEXT,
    schema_version INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY(processing_run_uuid) REFERENCES memory_processing_runs(processing_run_uuid),
    FOREIGN KEY(model_profile_uuid) REFERENCES model_profiles(model_profile_uuid)
);
CREATE INDEX IF NOT EXISTS idx_processing_stage_runs_processing
ON processing_stage_runs(processing_run_uuid, created_at);
CREATE INDEX IF NOT EXISTS idx_processing_stage_runs_source
ON processing_stage_runs(source_type, source_uuid, created_at);

CREATE TABLE IF NOT EXISTS embedding_outbox (
    outbox_uuid TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_uuid TEXT NOT NULL,
    payload_ijson TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    priority INTEGER NOT NULL DEFAULT 25,
    attempts INTEGER NOT NULL DEFAULT 0,
    run_after TEXT NOT NULL,
    locked_by TEXT,
    locked_at TEXT,
    last_error_sanitized TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1,
    UNIQUE(event_type, source_type, source_uuid)
);
CREATE INDEX IF NOT EXISTS idx_embedding_outbox_status_priority
ON embedding_outbox(status, priority DESC, run_after ASC, created_at ASC);
