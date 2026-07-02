CREATE TABLE IF NOT EXISTS prompt_execution_runs (
    prompt_execution_uuid TEXT PRIMARY KEY,
    prompt_id TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    stage TEXT NOT NULL,
    model_profile_uuid TEXT,
    model_role TEXT NOT NULL,
    provider_type TEXT NOT NULL,
    model_name TEXT NOT NULL,
    workspace_uuid TEXT,
    project_uuid TEXT,
    session_uuid TEXT,
    message_uuid TEXT,
    unit_uuid TEXT,
    annotation_uuid TEXT,
    route_uuid TEXT,
    import_run_uuid TEXT,
    job_uuid TEXT,
    input_hash TEXT NOT NULL,
    output_hash TEXT NOT NULL,
    input_ref TEXT,
    raw_output_ijson TEXT,
    validated_output_ijson TEXT,
    status TEXT NOT NULL,
    warnings_ijson TEXT NOT NULL,
    error_sanitized TEXT,
    latency_ms INTEGER,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY(model_profile_uuid) REFERENCES model_profiles(model_profile_uuid),
    FOREIGN KEY(workspace_uuid) REFERENCES workspaces(workspace_uuid),
    FOREIGN KEY(project_uuid) REFERENCES projects(project_uuid),
    FOREIGN KEY(session_uuid) REFERENCES sessions(session_uuid),
    FOREIGN KEY(message_uuid) REFERENCES messages(message_uuid),
    FOREIGN KEY(import_run_uuid) REFERENCES import_runs(import_run_uuid),
    FOREIGN KEY(job_uuid) REFERENCES jobs(job_uuid)
);

CREATE INDEX IF NOT EXISTS idx_prompt_execution_runs_prompt
ON prompt_execution_runs(prompt_id, prompt_version, created_at);

CREATE INDEX IF NOT EXISTS idx_prompt_execution_runs_model
ON prompt_execution_runs(model_profile_uuid, model_role, created_at);

CREATE INDEX IF NOT EXISTS idx_prompt_execution_runs_scope
ON prompt_execution_runs(workspace_uuid, project_uuid, session_uuid, created_at);

CREATE INDEX IF NOT EXISTS idx_prompt_execution_runs_message
ON prompt_execution_runs(message_uuid, created_at);

ALTER TABLE jakobson_analysis_runs ADD COLUMN prompt_execution_uuid TEXT;
ALTER TABLE memory_signal_routes ADD COLUMN prompt_execution_uuid TEXT;
ALTER TABLE memory_candidates ADD COLUMN prompt_execution_uuid TEXT;
ALTER TABLE memory_versions ADD COLUMN prompt_execution_uuid TEXT;
ALTER TABLE memory_block_build_runs ADD COLUMN prompt_execution_uuid TEXT;
ALTER TABLE memory_block_versions ADD COLUMN prompt_execution_uuid TEXT;
ALTER TABLE memory_context_attachments ADD COLUMN prompt_execution_uuid TEXT;

CREATE INDEX IF NOT EXISTS idx_jakobson_runs_prompt_execution
ON jakobson_analysis_runs(prompt_execution_uuid)
WHERE prompt_execution_uuid IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_memory_signal_routes_prompt_execution
ON memory_signal_routes(prompt_execution_uuid)
WHERE prompt_execution_uuid IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_memory_candidates_prompt_execution
ON memory_candidates(prompt_execution_uuid)
WHERE prompt_execution_uuid IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_memory_versions_prompt_execution
ON memory_versions(prompt_execution_uuid)
WHERE prompt_execution_uuid IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_memory_block_versions_prompt_execution
ON memory_block_versions(prompt_execution_uuid)
WHERE prompt_execution_uuid IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_memory_context_attachments_prompt_execution
ON memory_context_attachments(prompt_execution_uuid)
WHERE prompt_execution_uuid IS NOT NULL;

CREATE TABLE IF NOT EXISTS import_reconstruction_runs (
    import_reconstruction_run_uuid TEXT PRIMARY KEY,
    prompt_execution_uuid TEXT,
    import_run_uuid TEXT NOT NULL,
    provider TEXT NOT NULL,
    status TEXT NOT NULL,
    reconstruction_ijson TEXT,
    warnings_ijson TEXT NOT NULL,
    created_at TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY(prompt_execution_uuid) REFERENCES prompt_execution_runs(prompt_execution_uuid),
    FOREIGN KEY(import_run_uuid) REFERENCES import_runs(import_run_uuid)
);

CREATE INDEX IF NOT EXISTS idx_import_reconstruction_runs_import
ON import_reconstruction_runs(import_run_uuid, created_at);

CREATE TABLE IF NOT EXISTS privacy_sensitivity_reviews (
    privacy_sensitivity_review_uuid TEXT PRIMARY KEY,
    prompt_execution_uuid TEXT,
    candidate_uuid TEXT,
    memory_uuid TEXT,
    sensitivity_level TEXT NOT NULL,
    allowed_storage TEXT NOT NULL,
    allowed_retrieval TEXT NOT NULL,
    review_ijson TEXT NOT NULL,
    created_at TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY(prompt_execution_uuid) REFERENCES prompt_execution_runs(prompt_execution_uuid),
    FOREIGN KEY(candidate_uuid) REFERENCES memory_candidates(candidate_uuid),
    FOREIGN KEY(memory_uuid) REFERENCES memories(memory_uuid)
);

CREATE INDEX IF NOT EXISTS idx_privacy_sensitivity_reviews_candidate
ON privacy_sensitivity_reviews(candidate_uuid, created_at)
WHERE candidate_uuid IS NOT NULL;
