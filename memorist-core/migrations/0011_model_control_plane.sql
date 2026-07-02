ALTER TABLE model_profiles ADD COLUMN provider_type TEXT NOT NULL DEFAULT 'unknown';
ALTER TABLE model_profiles ADD COLUMN provider_name TEXT;
ALTER TABLE model_profiles ADD COLUMN endpoint_is_local INTEGER NOT NULL DEFAULT 0;
ALTER TABLE model_profiles ADD COLUMN context_window INTEGER;
ALTER TABLE model_profiles ADD COLUMN max_input_tokens INTEGER;
ALTER TABLE model_profiles ADD COLUMN max_output_tokens INTEGER;
ALTER TABLE model_profiles ADD COLUMN embedding_dimension INTEGER;
ALTER TABLE model_profiles ADD COLUMN tokenizer_family TEXT;
ALTER TABLE model_profiles ADD COLUMN quality_profile TEXT NOT NULL DEFAULT 'unknown';
ALTER TABLE model_profiles ADD COLUMN latency_profile TEXT NOT NULL DEFAULT 'unknown';
ALTER TABLE model_profiles ADD COLUMN cost_profile_ijson TEXT;
ALTER TABLE model_profiles ADD COLUMN privacy_profile_ijson TEXT;
ALTER TABLE model_profiles ADD COLUMN secret_strategy TEXT NOT NULL DEFAULT 'none';
ALTER TABLE model_profiles ADD COLUMN secret_env_var_name TEXT;
ALTER TABLE model_profiles ADD COLUMN is_enabled INTEGER NOT NULL DEFAULT 1;
ALTER TABLE model_profiles ADD COLUMN privacy_acknowledged_at TEXT;

UPDATE model_profiles
SET provider_type = CASE
        WHEN LOWER(provider) IN ('deterministic', 'disabled', 'ollama')
            THEN LOWER(provider)
        WHEN LOWER(provider) = 'local_embedding'
            THEN 'deterministic'
        WHEN endpoint_url IS NOT NULL
            THEN 'openai_compatible_llm'
        ELSE 'unknown'
    END,
    provider_name = provider,
    endpoint_is_local = is_local,
    cost_profile_ijson = COALESCE(pricing_ijson, '{"currency":"USD","free_local":false}'),
    privacy_profile_ijson = COALESCE(
        metadata_ijson,
        '{"local_only":true,"requires_user_acknowledgement":false,"risk_level":"low"}'
    );

CREATE TABLE IF NOT EXISTS model_role_defaults (
    model_role_default_uuid TEXT PRIMARY KEY,
    role TEXT NOT NULL,
    model_profile_uuid TEXT NOT NULL,
    workspace_uuid TEXT,
    project_uuid TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT,
    schema_version INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY(model_profile_uuid) REFERENCES model_profiles(model_profile_uuid),
    FOREIGN KEY(workspace_uuid) REFERENCES workspaces(workspace_uuid),
    FOREIGN KEY(project_uuid) REFERENCES projects(project_uuid)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_model_role_defaults_scope
ON model_role_defaults(role, COALESCE(workspace_uuid, ''), COALESCE(project_uuid, ''));

CREATE TABLE IF NOT EXISTS model_usage_events (
    usage_uuid TEXT PRIMARY KEY,
    model_profile_uuid TEXT,
    role TEXT NOT NULL,
    stage TEXT NOT NULL,
    provider_type TEXT NOT NULL,
    model_name TEXT NOT NULL,
    workspace_uuid TEXT,
    project_uuid TEXT,
    session_uuid TEXT,
    message_uuid TEXT,
    import_run_uuid TEXT,
    attachment_uuid TEXT,
    job_uuid TEXT,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    embedding_count INTEGER NOT NULL DEFAULT 0,
    estimated_cost REAL,
    latency_ms INTEGER,
    status TEXT NOT NULL,
    error_class TEXT,
    error_message_sanitized TEXT,
    created_at TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY(model_profile_uuid) REFERENCES model_profiles(model_profile_uuid),
    FOREIGN KEY(workspace_uuid) REFERENCES workspaces(workspace_uuid),
    FOREIGN KEY(project_uuid) REFERENCES projects(project_uuid),
    FOREIGN KEY(session_uuid) REFERENCES sessions(session_uuid),
    FOREIGN KEY(message_uuid) REFERENCES messages(message_uuid),
    FOREIGN KEY(import_run_uuid) REFERENCES import_runs(import_run_uuid),
    FOREIGN KEY(attachment_uuid) REFERENCES memory_context_attachments(attachment_uuid),
    FOREIGN KEY(job_uuid) REFERENCES jobs(job_uuid)
);

CREATE INDEX IF NOT EXISTS idx_model_usage_events_role_created
ON model_usage_events(role, created_at);

CREATE TABLE IF NOT EXISTS embedding_records (
    embedding_record_uuid TEXT PRIMARY KEY,
    model_profile_uuid TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_uuid TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    vector_store_ref TEXT NOT NULL,
    created_at TEXT NOT NULL,
    stale_at TEXT,
    schema_version INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY(model_profile_uuid) REFERENCES model_profiles(model_profile_uuid)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_embedding_records_source_profile
ON embedding_records(source_type, source_uuid, model_profile_uuid, content_hash);
