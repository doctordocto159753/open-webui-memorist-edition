ALTER TABLE model_profiles ADD COLUMN IF NOT EXISTS profile_name TEXT;
ALTER TABLE model_profiles ADD COLUMN IF NOT EXISTS provider_type TEXT NOT NULL DEFAULT 'unknown';
ALTER TABLE model_profiles ADD COLUMN IF NOT EXISTS provider_name TEXT;
ALTER TABLE model_profiles ADD COLUMN IF NOT EXISTS endpoint_is_local BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE model_profiles ADD COLUMN IF NOT EXISTS context_window INTEGER;
ALTER TABLE model_profiles ADD COLUMN IF NOT EXISTS max_input_tokens INTEGER;
ALTER TABLE model_profiles ADD COLUMN IF NOT EXISTS max_output_tokens INTEGER;
ALTER TABLE model_profiles ADD COLUMN IF NOT EXISTS embedding_dimension INTEGER;
ALTER TABLE model_profiles ADD COLUMN IF NOT EXISTS tokenizer_family TEXT;
ALTER TABLE model_profiles ADD COLUMN IF NOT EXISTS quality_profile TEXT NOT NULL DEFAULT 'unknown';
ALTER TABLE model_profiles ADD COLUMN IF NOT EXISTS latency_profile TEXT NOT NULL DEFAULT 'unknown';
ALTER TABLE model_profiles ADD COLUMN IF NOT EXISTS quality_profile_jsonb JSONB;
ALTER TABLE model_profiles ADD COLUMN IF NOT EXISTS latency_profile_jsonb JSONB;
ALTER TABLE model_profiles ADD COLUMN IF NOT EXISTS cost_profile_jsonb JSONB;
ALTER TABLE model_profiles ADD COLUMN IF NOT EXISTS privacy_profile_jsonb JSONB;
ALTER TABLE model_profiles ADD COLUMN IF NOT EXISTS secret_strategy TEXT NOT NULL DEFAULT 'none';
ALTER TABLE model_profiles ADD COLUMN IF NOT EXISTS secret_env_var_name TEXT;
ALTER TABLE model_profiles ADD COLUMN IF NOT EXISTS requires_privacy_acknowledgement BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE model_profiles ADD COLUMN IF NOT EXISTS is_enabled BOOLEAN NOT NULL DEFAULT true;
ALTER TABLE model_profiles ADD COLUMN IF NOT EXISTS privacy_acknowledged_at TIMESTAMPTZ;

ALTER TABLE model_role_defaults ADD COLUMN IF NOT EXISTS workspace_uuid TEXT REFERENCES workspaces(workspace_uuid);
ALTER TABLE model_role_defaults ADD COLUMN IF NOT EXISTS project_uuid TEXT REFERENCES projects(project_uuid);

ALTER TABLE model_usage_events ADD COLUMN IF NOT EXISTS stage TEXT;
ALTER TABLE model_usage_events ADD COLUMN IF NOT EXISTS provider_type TEXT NOT NULL DEFAULT 'unknown';
ALTER TABLE model_usage_events ADD COLUMN IF NOT EXISTS model_name TEXT NOT NULL DEFAULT 'unknown';
ALTER TABLE model_usage_events ADD COLUMN IF NOT EXISTS workspace_uuid TEXT REFERENCES workspaces(workspace_uuid);
ALTER TABLE model_usage_events ADD COLUMN IF NOT EXISTS project_uuid TEXT REFERENCES projects(project_uuid);
ALTER TABLE model_usage_events ADD COLUMN IF NOT EXISTS session_uuid TEXT REFERENCES sessions(session_uuid);
ALTER TABLE model_usage_events ADD COLUMN IF NOT EXISTS message_uuid TEXT REFERENCES messages(message_uuid);
ALTER TABLE model_usage_events ADD COLUMN IF NOT EXISTS unit_uuid TEXT;
ALTER TABLE model_usage_events ADD COLUMN IF NOT EXISTS annotation_uuid TEXT;
ALTER TABLE model_usage_events ADD COLUMN IF NOT EXISTS import_run_uuid TEXT REFERENCES import_runs(import_run_uuid);
ALTER TABLE model_usage_events ADD COLUMN IF NOT EXISTS attachment_uuid TEXT REFERENCES memory_context_attachments(attachment_uuid);
ALTER TABLE model_usage_events ADD COLUMN IF NOT EXISTS job_uuid TEXT REFERENCES jobs(job_uuid);
ALTER TABLE model_usage_events ADD COLUMN IF NOT EXISTS embedding_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE model_usage_events ADD COLUMN IF NOT EXISTS estimated_cost DOUBLE PRECISION;
ALTER TABLE model_usage_events ADD COLUMN IF NOT EXISTS latency_ms INTEGER;
ALTER TABLE model_usage_events ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'ok';
ALTER TABLE model_usage_events ADD COLUMN IF NOT EXISTS error_class TEXT;
ALTER TABLE model_usage_events ADD COLUMN IF NOT EXISTS error_message_sanitized TEXT;

CREATE TABLE IF NOT EXISTS model_health_events (
    health_event_uuid TEXT PRIMARY KEY,
    model_profile_uuid TEXT REFERENCES model_profiles(model_profile_uuid),
    role TEXT NOT NULL,
    provider_type TEXT NOT NULL,
    model_name TEXT NOT NULL,
    status TEXT NOT NULL,
    latency_ms INTEGER,
    local_only_safe BOOLEAN NOT NULL DEFAULT true,
    detail_sanitized TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    schema_version INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_model_health_events_profile_created
ON model_health_events(model_profile_uuid, created_at);

CREATE TABLE IF NOT EXISTS model_privacy_acknowledgements (
    ack_uuid TEXT PRIMARY KEY,
    model_profile_uuid TEXT NOT NULL REFERENCES model_profiles(model_profile_uuid),
    role TEXT NOT NULL,
    acknowledged_risk_level TEXT NOT NULL,
    acknowledged_data_sent_jsonb JSONB NOT NULL,
    acknowledged_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    schema_version INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_model_privacy_acknowledgements_profile_created
ON model_privacy_acknowledgements(model_profile_uuid, created_at);

CREATE TABLE IF NOT EXISTS embedding_records (
    embedding_record_uuid TEXT PRIMARY KEY,
    model_profile_uuid TEXT NOT NULL REFERENCES model_profiles(model_profile_uuid),
    source_type TEXT NOT NULL,
    source_uuid TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    embedding_dimension INTEGER,
    vector_store_ref TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    stale_at TIMESTAMPTZ,
    schema_version INTEGER NOT NULL DEFAULT 1
);
