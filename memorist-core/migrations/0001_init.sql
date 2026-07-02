CREATE TABLE IF NOT EXISTS workspaces (
    workspace_uuid TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT,
    schema_version INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS projects (
    project_uuid TEXT PRIMARY KEY,
    workspace_uuid TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    updated_at TEXT,
    schema_version INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY(workspace_uuid) REFERENCES workspaces(workspace_uuid)
);

CREATE TABLE IF NOT EXISTS sessions (
    session_uuid TEXT PRIMARY KEY,
    workspace_uuid TEXT,
    project_uuid TEXT,
    openwebui_conversation_id TEXT,
    title TEXT,
    source_app TEXT NOT NULL DEFAULT 'open_webui',
    created_at TEXT NOT NULL,
    updated_at TEXT,
    conceptual_state_text TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    privacy_scope TEXT NOT NULL DEFAULT 'local',
    active_model_profile_uuid TEXT,
    content_hash TEXT,
    schema_version INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY(workspace_uuid) REFERENCES workspaces(workspace_uuid),
    FOREIGN KEY(project_uuid) REFERENCES projects(project_uuid)
);

CREATE TABLE IF NOT EXISTS session_events (
    event_uuid TEXT PRIMARY KEY,
    session_uuid TEXT NOT NULL,
    event_type TEXT NOT NULL,
    event_index INTEGER NOT NULL,
    payload_ijson TEXT NOT NULL,
    actor_type TEXT,
    actor_uuid TEXT,
    created_at TEXT NOT NULL,
    content_hash TEXT,
    schema_version INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY(session_uuid) REFERENCES sessions(session_uuid)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_session_events_order
ON session_events(session_uuid, event_index);

CREATE TABLE IF NOT EXISTS messages (
    message_uuid TEXT PRIMARY KEY,
    session_uuid TEXT NOT NULL,
    parent_message_uuid TEXT,
    turn_index INTEGER,
    role TEXT NOT NULL,
    creator_type TEXT NOT NULL,
    model_profile_uuid TEXT,
    conceptual_linguistic_model_uuid TEXT,
    raw_text TEXT,
    raw_payload_ijson TEXT,
    snapshot_ijson TEXT,
    content_hash TEXT,
    token_count_input INTEGER DEFAULT 0,
    token_count_output INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT,
    processing_status TEXT NOT NULL DEFAULT 'pending',
    visibility TEXT NOT NULL DEFAULT 'visible',
    is_deleted INTEGER NOT NULL DEFAULT 0,
    redaction_status TEXT NOT NULL DEFAULT 'none',
    schema_version INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY(session_uuid) REFERENCES sessions(session_uuid)
);

CREATE INDEX IF NOT EXISTS idx_messages_session_turn
ON messages(session_uuid, turn_index);

CREATE INDEX IF NOT EXISTS idx_messages_processing_status
ON messages(processing_status);

CREATE INDEX IF NOT EXISTS idx_messages_role
ON messages(role);

CREATE TABLE IF NOT EXISTS message_versions (
    message_version_uuid TEXT PRIMARY KEY,
    message_uuid TEXT NOT NULL,
    version_number INTEGER NOT NULL,
    raw_text TEXT,
    raw_payload_ijson TEXT,
    snapshot_ijson TEXT,
    change_reason TEXT,
    created_by TEXT,
    created_at TEXT NOT NULL,
    content_hash TEXT,
    schema_version INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY(message_uuid) REFERENCES messages(message_uuid)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_message_versions_number
ON message_versions(message_uuid, version_number);

CREATE TABLE IF NOT EXISTS model_profiles (
    model_profile_uuid TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    model_name TEXT NOT NULL,
    role TEXT NOT NULL,
    endpoint_url TEXT,
    is_local INTEGER NOT NULL DEFAULT 0,
    supports_structured_output INTEGER NOT NULL DEFAULT 0,
    supports_json_mode INTEGER NOT NULL DEFAULT 0,
    supports_embeddings INTEGER NOT NULL DEFAULT 0,
    pricing_ijson TEXT,
    metadata_ijson TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT,
    schema_version INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS jobs (
    job_uuid TEXT PRIMARY KEY,
    job_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    priority INTEGER NOT NULL DEFAULT 50,
    payload_ijson TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    last_error TEXT,
    run_after TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_jobs_status_priority
ON jobs(status, priority, run_after);

CREATE INDEX IF NOT EXISTS idx_jobs_type
ON jobs(job_type);

CREATE TABLE IF NOT EXISTS memory_context_attachments (
    attachment_uuid TEXT PRIMARY KEY,
    session_uuid TEXT NOT NULL,
    project_uuid TEXT,
    input_message_uuid TEXT,
    retrieval_run_uuid TEXT,
    attachment_mode TEXT NOT NULL,
    ijson_attachment TEXT NOT NULL,
    rendered_attachment TEXT,
    source_block_uuids_ijson TEXT,
    source_memory_uuids_ijson TEXT,
    source_fact_edge_uuids_ijson TEXT,
    token_count INTEGER DEFAULT 0,
    confidence REAL DEFAULT 0.5,
    abstention_status TEXT,
    created_at TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY(session_uuid) REFERENCES sessions(session_uuid)
);

CREATE TABLE IF NOT EXISTS memory_blocks (
    block_uuid TEXT PRIMARY KEY,
    block_type TEXT NOT NULL,
    scope_type TEXT NOT NULL,
    scope_uuid TEXT,
    label TEXT NOT NULL,
    description TEXT,
    value TEXT NOT NULL,
    char_limit INTEGER NOT NULL,
    priority INTEGER DEFAULT 50,
    read_only INTEGER DEFAULT 0,
    always_in_context INTEGER DEFAULT 1,
    source_memory_uuids_ijson TEXT,
    source_nucleus_uuids_ijson TEXT,
    source_fact_edge_uuids_ijson TEXT,
    last_compacted_at TEXT,
    last_used_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT,
    status TEXT DEFAULT 'active',
    schema_version INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS session_hot_cache (
    session_uuid TEXT PRIMARY KEY,
    current_problem TEXT,
    last_user_intent TEXT,
    active_constraints_ijson TEXT,
    unresolved_questions_ijson TEXT,
    recent_entities_ijson TEXT,
    current_topic_nucleus_uuid TEXT,
    recent_memory_uuids_ijson TEXT,
    current_plan TEXT,
    last_updated_at TEXT NOT NULL,
    checkpoint_event_uuid TEXT,
    schema_version INTEGER DEFAULT 1,
    FOREIGN KEY(session_uuid) REFERENCES sessions(session_uuid)
);
