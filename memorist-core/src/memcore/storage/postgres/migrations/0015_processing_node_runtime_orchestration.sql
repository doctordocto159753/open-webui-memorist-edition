ALTER TABLE model_profiles ADD COLUMN IF NOT EXISTS setup_idempotency_key TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS uq_model_profiles_setup_idempotency
ON model_profiles(setup_idempotency_key)
WHERE setup_idempotency_key IS NOT NULL;

ALTER TABLE model_health_events ADD COLUMN IF NOT EXISTS result_jsonb JSONB;
ALTER TABLE model_health_events ADD COLUMN IF NOT EXISTS test_idempotency_key TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS uq_model_health_test_idempotency
ON model_health_events(model_profile_uuid, test_idempotency_key)
WHERE test_idempotency_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS processing_stage_runs (
    stage_execution_uuid TEXT PRIMARY KEY,
    processing_run_uuid TEXT REFERENCES memory_processing_runs(processing_run_uuid),
    source_type TEXT NOT NULL,
    source_uuid TEXT NOT NULL,
    requested_role TEXT NOT NULL,
    effective_role TEXT NOT NULL,
    stage TEXT NOT NULL,
    model_profile_uuid TEXT REFERENCES model_profiles(model_profile_uuid),
    provider_type TEXT NOT NULL,
    model_name TEXT NOT NULL,
    prompt_id TEXT,
    prompt_version TEXT,
    input_hash TEXT NOT NULL,
    output_hash TEXT,
    status TEXT NOT NULL,
    called_provider BOOLEAN NOT NULL DEFAULT false,
    fallback_used BOOLEAN NOT NULL DEFAULT false,
    scope_source TEXT NOT NULL,
    inheritance_source TEXT,
    fallback_reason TEXT,
    detail_sanitized TEXT,
    validation_errors_jsonb JSONB,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    embedding_count INTEGER NOT NULL DEFAULT 0,
    latency_ms INTEGER NOT NULL DEFAULT 0,
    idempotency_key TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ,
    schema_version INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_processing_stage_runs_processing
ON processing_stage_runs(processing_run_uuid, created_at);
CREATE INDEX IF NOT EXISTS idx_processing_stage_runs_source
ON processing_stage_runs(source_type, source_uuid, created_at);

CREATE TABLE IF NOT EXISTS memory_version_embeddings (
    memory_version_uuid TEXT NOT NULL REFERENCES memory_versions(memory_version_uuid),
    embedding_model TEXT NOT NULL,
    embedding_dimension INTEGER NOT NULL,
    embedding_version TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    embedding_jsonb JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    schema_version INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY(memory_version_uuid, embedding_model, embedding_version)
);
