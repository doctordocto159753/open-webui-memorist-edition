CREATE TABLE IF NOT EXISTS import_message_processing_status (
    status_uuid TEXT PRIMARY KEY,
    import_run_uuid TEXT NOT NULL,
    source_platform TEXT NOT NULL,
    source_conversation_id TEXT,
    source_message_id TEXT,
    target_session_uuid TEXT,
    target_message_uuid TEXT,
    processing_mode TEXT NOT NULL,
    processing_stage TEXT NOT NULL DEFAULT 'eligibility',
    status TEXT NOT NULL,
    skip_reason TEXT,
    job_uuid TEXT,
    memory_processing_run_uuid TEXT,
    prompt_execution_uuid TEXT,
    model_profile_uuid TEXT,
    provider_type TEXT,
    model_name TEXT,
    input_content_hash TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0,
    error_sanitized TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    finished_at TEXT,
    schema_version INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY(import_run_uuid) REFERENCES import_runs(import_run_uuid),
    FOREIGN KEY(target_session_uuid) REFERENCES sessions(session_uuid),
    FOREIGN KEY(target_message_uuid) REFERENCES messages(message_uuid),
    FOREIGN KEY(job_uuid) REFERENCES jobs(job_uuid),
    FOREIGN KEY(memory_processing_run_uuid)
        REFERENCES memory_processing_runs(processing_run_uuid),
    FOREIGN KEY(prompt_execution_uuid)
        REFERENCES prompt_execution_runs(prompt_execution_uuid),
    FOREIGN KEY(model_profile_uuid) REFERENCES model_profiles(model_profile_uuid)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_import_message_processing_identity
ON import_message_processing_status(
    import_run_uuid,
    source_platform,
    COALESCE(source_conversation_id, ''),
    COALESCE(source_message_id, ''),
    processing_mode
);

CREATE INDEX IF NOT EXISTS idx_import_message_processing_progress
ON import_message_processing_status(import_run_uuid, status, updated_at);

CREATE INDEX IF NOT EXISTS idx_import_message_processing_target
ON import_message_processing_status(target_message_uuid, status);
