CREATE TABLE IF NOT EXISTS workspaces (
    workspace_uuid TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    schema_version INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS projects (
    project_uuid TEXT PRIMARY KEY,
    workspace_uuid TEXT REFERENCES workspaces(workspace_uuid),
    name TEXT NOT NULL,
    description TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    schema_version INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS sessions (
    session_uuid TEXT PRIMARY KEY,
    workspace_uuid TEXT REFERENCES workspaces(workspace_uuid),
    project_uuid TEXT REFERENCES projects(project_uuid),
    openwebui_conversation_id TEXT,
    title TEXT,
    conceptual_state_text TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    schema_version INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS openwebui_session_aliases (
    alias_uuid TEXT PRIMARY KEY,
    session_uuid TEXT NOT NULL REFERENCES sessions(session_uuid),
    openwebui_chat_id TEXT NOT NULL,
    openwebui_user_id TEXT,
    alias_type TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    schema_version INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS session_events (
    event_uuid TEXT PRIMARY KEY,
    session_uuid TEXT NOT NULL REFERENCES sessions(session_uuid),
    event_type TEXT NOT NULL,
    event_index INTEGER NOT NULL,
    payload_jsonb JSONB NOT NULL,
    actor_type TEXT,
    actor_uuid TEXT,
    content_hash TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    schema_version INTEGER NOT NULL DEFAULT 1,
    UNIQUE(session_uuid, event_index)
);

CREATE TABLE IF NOT EXISTS model_profiles (
    model_profile_uuid TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    model_name TEXT NOT NULL,
    role TEXT NOT NULL,
    endpoint_url TEXT,
    is_local BOOLEAN NOT NULL DEFAULT false,
    supports_structured_output BOOLEAN NOT NULL DEFAULT false,
    supports_json_mode BOOLEAN NOT NULL DEFAULT false,
    supports_embeddings BOOLEAN NOT NULL DEFAULT false,
    pricing_jsonb JSONB,
    metadata_jsonb JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    schema_version INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS model_role_defaults (
    role_default_uuid TEXT PRIMARY KEY,
    role TEXT NOT NULL UNIQUE,
    model_profile_uuid TEXT NOT NULL REFERENCES model_profiles(model_profile_uuid),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    schema_version INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS model_usage_events (
    usage_event_uuid TEXT PRIMARY KEY,
    model_profile_uuid TEXT REFERENCES model_profiles(model_profile_uuid),
    role TEXT NOT NULL,
    event_type TEXT NOT NULL,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cost_jsonb JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    schema_version INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS messages (
    message_uuid TEXT PRIMARY KEY,
    session_uuid TEXT NOT NULL REFERENCES sessions(session_uuid),
    turn_index INTEGER NOT NULL,
    role TEXT NOT NULL,
    creator_type TEXT NOT NULL,
    model_profile_uuid TEXT REFERENCES model_profiles(model_profile_uuid),
    raw_text TEXT,
    raw_payload_jsonb JSONB,
    snapshot_jsonb JSONB,
    processing_status TEXT NOT NULL DEFAULT 'pending',
    visibility TEXT NOT NULL DEFAULT 'visible',
    is_deleted BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    schema_version INTEGER NOT NULL DEFAULT 1,
    UNIQUE(session_uuid, turn_index, role, creator_type)
);

CREATE TABLE IF NOT EXISTS message_versions (
    message_version_uuid TEXT PRIMARY KEY,
    message_uuid TEXT NOT NULL REFERENCES messages(message_uuid),
    version_number INTEGER NOT NULL,
    raw_text TEXT,
    raw_payload_jsonb JSONB,
    snapshot_jsonb JSONB,
    change_reason TEXT,
    created_by TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    schema_version INTEGER NOT NULL DEFAULT 1,
    UNIQUE(message_uuid, version_number)
);

CREATE TABLE IF NOT EXISTS memory_processing_runs (
    processing_run_uuid TEXT PRIMARY KEY,
    session_uuid TEXT NOT NULL REFERENCES sessions(session_uuid),
    message_uuid TEXT NOT NULL REFERENCES messages(message_uuid),
    pipeline_version TEXT NOT NULL,
    prompt_bundle_version TEXT,
    input_content_hash TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    error_sanitized TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    schema_version INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS text_units (
    text_unit_uuid TEXT PRIMARY KEY,
    unit_uuid TEXT UNIQUE,
    message_uuid TEXT NOT NULL REFERENCES messages(message_uuid),
    session_uuid TEXT NOT NULL REFERENCES sessions(session_uuid),
    speaker_role TEXT NOT NULL,
    unit_type TEXT NOT NULL,
    unit_index INTEGER NOT NULL,
    text TEXT NOT NULL,
    start_char INTEGER NOT NULL,
    end_char INTEGER NOT NULL,
    char_start INTEGER,
    char_end INTEGER,
    language_code TEXT,
    language_hint TEXT,
    segmentation_confidence TEXT,
    segmentation_notes TEXT,
    content_hash TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    schema_version INTEGER NOT NULL DEFAULT 1,
    UNIQUE(message_uuid, unit_type, unit_index)
);

CREATE TABLE IF NOT EXISTS jakobson_analysis_runs (
    analysis_run_uuid TEXT PRIMARY KEY,
    workspace_uuid TEXT REFERENCES workspaces(workspace_uuid),
    project_uuid TEXT REFERENCES projects(project_uuid),
    session_uuid TEXT NOT NULL REFERENCES sessions(session_uuid),
    message_uuid TEXT NOT NULL REFERENCES messages(message_uuid),
    prompt_id TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    model_profile_uuid TEXT REFERENCES model_profiles(model_profile_uuid),
    provider_type TEXT NOT NULL,
    model_name TEXT,
    input_hash TEXT NOT NULL,
    output_hash TEXT,
    status TEXT NOT NULL,
    warnings_jsonb JSONB NOT NULL DEFAULT '[]'::jsonb,
    raw_output_jsonb JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    schema_version INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS jakobson_sentence_annotations (
    annotation_uuid TEXT PRIMARY KEY,
    analysis_run_uuid TEXT NOT NULL REFERENCES jakobson_analysis_runs(analysis_run_uuid),
    message_uuid TEXT NOT NULL REFERENCES messages(message_uuid),
    unit_uuid TEXT NOT NULL REFERENCES text_units(text_unit_uuid),
    sentence_index INTEGER NOT NULL,
    sentence_text TEXT NOT NULL,
    sentence_hash TEXT NOT NULL,
    sender_value TEXT,
    sender_evidence TEXT,
    sender_confidence TEXT NOT NULL,
    receiver_value TEXT,
    receiver_evidence TEXT,
    receiver_confidence TEXT NOT NULL,
    message_value TEXT,
    message_evidence TEXT,
    message_confidence TEXT NOT NULL,
    context_value TEXT,
    context_evidence TEXT,
    context_confidence TEXT NOT NULL,
    code_value TEXT,
    code_evidence TEXT,
    code_confidence TEXT NOT NULL,
    contact_channel_value TEXT,
    contact_channel_evidence TEXT,
    contact_channel_confidence TEXT NOT NULL,
    dominant_function TEXT NOT NULL,
    secondary_functions_jsonb JSONB NOT NULL DEFAULT '[]'::jsonb,
    function_reason TEXT,
    notes TEXT,
    raw_sentence_output_jsonb JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    schema_version INTEGER NOT NULL DEFAULT 1,
    UNIQUE(analysis_run_uuid, unit_uuid)
);

CREATE TABLE IF NOT EXISTS memory_signal_routes (
    route_uuid TEXT PRIMARY KEY,
    annotation_uuid TEXT NOT NULL REFERENCES jakobson_sentence_annotations(annotation_uuid),
    message_uuid TEXT NOT NULL REFERENCES messages(message_uuid),
    unit_uuid TEXT NOT NULL REFERENCES text_units(text_unit_uuid),
    dominant_function TEXT NOT NULL,
    secondary_functions_jsonb JSONB NOT NULL DEFAULT '[]'::jsonb,
    route_type TEXT NOT NULL,
    extractor_id TEXT NOT NULL,
    priority INTEGER NOT NULL,
    confidence TEXT NOT NULL,
    reason TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'ready',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    schema_version INTEGER NOT NULL DEFAULT 1,
    UNIQUE(annotation_uuid, route_type, extractor_id)
);

CREATE TABLE IF NOT EXISTS memory_candidates (
    candidate_uuid TEXT PRIMARY KEY,
    processing_run_uuid TEXT NOT NULL REFERENCES memory_processing_runs(processing_run_uuid),
    text_unit_uuid TEXT NOT NULL REFERENCES text_units(text_unit_uuid),
    candidate_type TEXT NOT NULL,
    subject_key TEXT NOT NULL,
    predicate TEXT NOT NULL,
    object_jsonb JSONB NOT NULL,
    normalized_text TEXT NOT NULL,
    source_authority TEXT NOT NULL,
    explicitness TEXT NOT NULL,
    confidence DOUBLE PRECISION NOT NULL,
    importance DOUBLE PRECISION NOT NULL,
    sensitivity TEXT NOT NULL DEFAULT 'normal',
    status TEXT NOT NULL,
    rejection_reason TEXT,
    extraction_metadata_jsonb JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    schema_version INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS candidate_evidence (
    evidence_uuid TEXT PRIMARY KEY,
    candidate_uuid TEXT NOT NULL REFERENCES memory_candidates(candidate_uuid),
    message_uuid TEXT NOT NULL REFERENCES messages(message_uuid),
    text_unit_uuid TEXT NOT NULL REFERENCES text_units(text_unit_uuid),
    annotation_uuid TEXT REFERENCES jakobson_sentence_annotations(annotation_uuid),
    route_uuid TEXT REFERENCES memory_signal_routes(route_uuid),
    evidence_text TEXT NOT NULL,
    start_char INTEGER NOT NULL,
    end_char INTEGER NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    schema_version INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS memories (
    memory_uuid TEXT PRIMARY KEY,
    scope_type TEXT NOT NULL,
    scope_uuid TEXT,
    canonical_key TEXT NOT NULL,
    current_version_uuid TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    schema_version INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS memory_versions (
    memory_version_uuid TEXT PRIMARY KEY,
    memory_uuid TEXT NOT NULL REFERENCES memories(memory_uuid),
    version_number INTEGER NOT NULL,
    operation TEXT NOT NULL,
    value TEXT NOT NULL,
    normalized_text TEXT NOT NULL,
    confidence DOUBLE PRECISION NOT NULL,
    importance DOUBLE PRECISION NOT NULL,
    source_snapshot_hash TEXT NOT NULL,
    transaction_from TIMESTAMPTZ NOT NULL DEFAULT now(),
    transaction_until TIMESTAMPTZ,
    valid_from TIMESTAMPTZ,
    valid_until TIMESTAMPTZ,
    status TEXT NOT NULL DEFAULT 'current',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    schema_version INTEGER NOT NULL DEFAULT 1,
    UNIQUE(memory_uuid, version_number)
);

CREATE TABLE IF NOT EXISTS memory_evidence_links (
    link_uuid TEXT PRIMARY KEY,
    memory_uuid TEXT NOT NULL REFERENCES memories(memory_uuid),
    memory_version_uuid TEXT NOT NULL REFERENCES memory_versions(memory_version_uuid),
    candidate_uuid TEXT NOT NULL REFERENCES memory_candidates(candidate_uuid),
    evidence_uuid TEXT NOT NULL REFERENCES candidate_evidence(evidence_uuid),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    schema_version INTEGER NOT NULL DEFAULT 1,
    UNIQUE(memory_version_uuid, evidence_uuid)
);

CREATE TABLE IF NOT EXISTS memory_blocks (
    block_uuid TEXT PRIMARY KEY,
    scope_type TEXT NOT NULL,
    scope_uuid TEXT,
    block_type TEXT NOT NULL,
    canonical_key TEXT NOT NULL,
    current_version_uuid TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    optimistic_lock_version INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    schema_version INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS memory_block_versions (
    block_version_uuid TEXT PRIMARY KEY,
    block_uuid TEXT NOT NULL REFERENCES memory_blocks(block_uuid),
    version_number INTEGER NOT NULL,
    value TEXT NOT NULL,
    source_snapshot_hash TEXT NOT NULL,
    active_constraints_jsonb JSONB,
    unresolved_questions_jsonb JSONB,
    recent_entities_jsonb JSONB,
    recent_memory_uuids_jsonb JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    schema_version INTEGER NOT NULL DEFAULT 1,
    UNIQUE(block_uuid, version_number)
);

CREATE TABLE IF NOT EXISTS memory_block_sources (
    block_source_uuid TEXT PRIMARY KEY,
    block_version_uuid TEXT NOT NULL REFERENCES memory_block_versions(block_version_uuid),
    memory_uuid TEXT NOT NULL REFERENCES memories(memory_uuid),
    memory_version_uuid TEXT NOT NULL REFERENCES memory_versions(memory_version_uuid),
    source_role TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    schema_version INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS memory_context_attachments (
    attachment_uuid TEXT PRIMARY KEY,
    session_uuid TEXT NOT NULL REFERENCES sessions(session_uuid),
    input_message_uuid TEXT NOT NULL REFERENCES messages(message_uuid),
    retrieval_run_uuid TEXT,
    attachment_jsonb JSONB NOT NULL,
    token_count INTEGER NOT NULL DEFAULT 0,
    mode TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    schema_version INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS retrieval_runs (
    retrieval_run_uuid TEXT PRIMARY KEY,
    session_uuid TEXT NOT NULL REFERENCES sessions(session_uuid),
    project_uuid TEXT REFERENCES projects(project_uuid),
    input_message_uuid TEXT NOT NULL REFERENCES messages(message_uuid),
    retrieval_mode TEXT NOT NULL,
    original_query TEXT NOT NULL,
    token_budget INTEGER NOT NULL,
    status TEXT NOT NULL,
    planner_version TEXT NOT NULL,
    config_snapshot_jsonb JSONB NOT NULL,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    latency_ms INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    schema_version INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS retrieval_candidates (
    retrieval_candidate_uuid TEXT PRIMARY KEY,
    retrieval_run_uuid TEXT NOT NULL REFERENCES retrieval_runs(retrieval_run_uuid),
    memory_uuid TEXT NOT NULL REFERENCES memories(memory_uuid),
    memory_version_uuid TEXT NOT NULL REFERENCES memory_versions(memory_version_uuid),
    generator_type TEXT NOT NULL,
    generator_rank INTEGER,
    lexical_score DOUBLE PRECISION,
    semantic_score DOUBLE PRECISION,
    graph_score DOUBLE PRECISION,
    fused_score DOUBLE PRECISION,
    final_score DOUBLE PRECISION,
    decision TEXT,
    score_trace_jsonb JSONB,
    source_channel TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    schema_version INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS import_runs (
    import_run_uuid TEXT PRIMARY KEY,
    source_platform TEXT NOT NULL,
    archive_hash TEXT NOT NULL,
    status TEXT NOT NULL,
    total_records INTEGER NOT NULL DEFAULT 0,
    processed_records INTEGER NOT NULL DEFAULT 0,
    failed_records INTEGER NOT NULL DEFAULT 0,
    current_batch INTEGER NOT NULL DEFAULT 0,
    manifest_jsonb JSONB,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    error_sanitized TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    schema_version INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS import_records (
    import_record_uuid TEXT PRIMARY KEY,
    import_run_uuid TEXT NOT NULL REFERENCES import_runs(import_run_uuid),
    source_record_id TEXT NOT NULL,
    record_type TEXT NOT NULL,
    status TEXT NOT NULL,
    payload_jsonb JSONB NOT NULL,
    normalized_hash TEXT NOT NULL,
    error_sanitized TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    schema_version INTEGER NOT NULL DEFAULT 1,
    UNIQUE(import_run_uuid, source_record_id)
);

CREATE TABLE IF NOT EXISTS import_mappings (
    mapping_uuid TEXT PRIMARY KEY,
    import_run_uuid TEXT NOT NULL REFERENCES import_runs(import_run_uuid),
    import_record_uuid TEXT REFERENCES import_records(import_record_uuid),
    source_type TEXT NOT NULL,
    source_uuid TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_uuid TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    schema_version INTEGER NOT NULL DEFAULT 1,
    UNIQUE(import_run_uuid, source_type, source_uuid, target_type)
);

CREATE TABLE IF NOT EXISTS import_commit_batches (
    batch_uuid TEXT PRIMARY KEY,
    import_run_uuid TEXT NOT NULL REFERENCES import_runs(import_run_uuid),
    batch_number INTEGER NOT NULL,
    batch_fingerprint TEXT NOT NULL,
    status TEXT NOT NULL,
    record_count INTEGER NOT NULL,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    error_sanitized TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    schema_version INTEGER NOT NULL DEFAULT 1,
    UNIQUE(import_run_uuid, batch_number),
    UNIQUE(import_run_uuid, batch_fingerprint)
);

CREATE TABLE IF NOT EXISTS privacy_requests (
    privacy_request_uuid TEXT PRIMARY KEY,
    request_type TEXT NOT NULL,
    status TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_uuid TEXT NOT NULL,
    preview_jsonb JSONB,
    receipt_uuid TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    schema_version INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS privacy_request_items (
    item_uuid TEXT PRIMARY KEY,
    privacy_request_uuid TEXT NOT NULL REFERENCES privacy_requests(privacy_request_uuid),
    target_type TEXT NOT NULL,
    target_uuid TEXT NOT NULL,
    action TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    schema_version INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS erasure_receipts (
    receipt_uuid TEXT PRIMARY KEY,
    privacy_request_uuid TEXT NOT NULL REFERENCES privacy_requests(privacy_request_uuid),
    receipt_jsonb JSONB NOT NULL,
    residue_report_jsonb JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    schema_version INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS jobs (
    job_uuid TEXT PRIMARY KEY,
    job_type TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 50,
    status TEXT NOT NULL DEFAULT 'pending',
    payload_jsonb JSONB NOT NULL,
    idempotency_key TEXT,
    run_after TIMESTAMPTZ NOT NULL DEFAULT now(),
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    locked_by TEXT,
    locked_at TIMESTAMPTZ,
    last_error_sanitized TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    schema_version INTEGER NOT NULL DEFAULT 1,
    UNIQUE(job_type, idempotency_key)
);

CREATE TABLE IF NOT EXISTS job_attempts (
    job_attempt_uuid TEXT PRIMARY KEY,
    job_uuid TEXT NOT NULL REFERENCES jobs(job_uuid),
    worker_id TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    error_sanitized TEXT,
    schema_version INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS graph_projection_outbox (
    outbox_uuid TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_uuid TEXT NOT NULL,
    payload_jsonb JSONB NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    priority INTEGER NOT NULL DEFAULT 50,
    attempts INTEGER NOT NULL DEFAULT 0,
    run_after TIMESTAMPTZ NOT NULL DEFAULT now(),
    locked_by TEXT,
    locked_at TIMESTAMPTZ,
    last_error_sanitized TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    schema_version INTEGER NOT NULL DEFAULT 1,
    UNIQUE(event_type, source_type, source_uuid)
);

CREATE TABLE IF NOT EXISTS embedding_outbox (
    outbox_uuid TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_uuid TEXT NOT NULL,
    payload_jsonb JSONB NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    priority INTEGER NOT NULL DEFAULT 25,
    attempts INTEGER NOT NULL DEFAULT 0,
    run_after TIMESTAMPTZ NOT NULL DEFAULT now(),
    locked_by TEXT,
    locked_at TIMESTAMPTZ,
    last_error_sanitized TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    schema_version INTEGER NOT NULL DEFAULT 1,
    UNIQUE(event_type, source_type, source_uuid)
);

CREATE TABLE IF NOT EXISTS memory_processing_outbox (
    outbox_uuid TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_uuid TEXT NOT NULL,
    payload_jsonb JSONB NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    priority INTEGER NOT NULL DEFAULT 60,
    attempts INTEGER NOT NULL DEFAULT 0,
    run_after TIMESTAMPTZ NOT NULL DEFAULT now(),
    locked_by TEXT,
    locked_at TIMESTAMPTZ,
    last_error_sanitized TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    schema_version INTEGER NOT NULL DEFAULT 1,
    UNIQUE(event_type, source_type, source_uuid)
);

CREATE TABLE IF NOT EXISTS privacy_erasure_outbox (
    outbox_uuid TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_uuid TEXT NOT NULL,
    payload_jsonb JSONB NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    priority INTEGER NOT NULL DEFAULT 110,
    attempts INTEGER NOT NULL DEFAULT 0,
    run_after TIMESTAMPTZ NOT NULL DEFAULT now(),
    locked_by TEXT,
    locked_at TIMESTAMPTZ,
    last_error_sanitized TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    schema_version INTEGER NOT NULL DEFAULT 1,
    UNIQUE(event_type, source_type, source_uuid)
);

CREATE TABLE IF NOT EXISTS graph_projection_state (
    projection_state_uuid TEXT PRIMARY KEY,
    source_uuid TEXT NOT NULL,
    source_type TEXT NOT NULL,
    projection_version TEXT NOT NULL,
    graph_key TEXT NOT NULL,
    status TEXT NOT NULL,
    last_projected_at TIMESTAMPTZ,
    last_error_sanitized TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    schema_version INTEGER NOT NULL DEFAULT 1,
    UNIQUE(source_type, source_uuid, projection_version)
);

CREATE TABLE IF NOT EXISTS cost_events (
    cost_event_uuid TEXT PRIMARY KEY,
    model_profile_uuid TEXT REFERENCES model_profiles(model_profile_uuid),
    role TEXT NOT NULL,
    amount_jsonb JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    schema_version INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_projects_workspace_uuid ON projects(workspace_uuid);
CREATE UNIQUE INDEX IF NOT EXISTS idx_openwebui_alias_identity
    ON openwebui_session_aliases(openwebui_chat_id, COALESCE(openwebui_user_id, ''));
CREATE INDEX IF NOT EXISTS idx_sessions_workspace_uuid ON sessions(workspace_uuid);
CREATE INDEX IF NOT EXISTS idx_sessions_project_uuid ON sessions(project_uuid);
CREATE INDEX IF NOT EXISTS idx_messages_session_uuid ON messages(session_uuid);
CREATE INDEX IF NOT EXISTS idx_messages_message_uuid ON messages(message_uuid);
CREATE INDEX IF NOT EXISTS idx_messages_created_at ON messages(created_at);
CREATE INDEX IF NOT EXISTS idx_text_units_message_uuid ON text_units(message_uuid);
CREATE INDEX IF NOT EXISTS idx_text_units_unit_uuid ON text_units(unit_uuid);
CREATE INDEX IF NOT EXISTS idx_jakobson_runs_message_uuid ON jakobson_analysis_runs(message_uuid);
CREATE INDEX IF NOT EXISTS idx_jakobson_annotations_annotation_uuid ON jakobson_sentence_annotations(annotation_uuid);
CREATE INDEX IF NOT EXISTS idx_jakobson_annotations_message_uuid ON jakobson_sentence_annotations(message_uuid);
CREATE INDEX IF NOT EXISTS idx_jakobson_annotations_unit_uuid ON jakobson_sentence_annotations(unit_uuid);
CREATE INDEX IF NOT EXISTS idx_jakobson_annotations_dominant_function ON jakobson_sentence_annotations(dominant_function);
CREATE INDEX IF NOT EXISTS idx_jakobson_annotations_receiver_value ON jakobson_sentence_annotations(receiver_value);
CREATE INDEX IF NOT EXISTS idx_jakobson_annotations_context_value ON jakobson_sentence_annotations(context_value);
CREATE INDEX IF NOT EXISTS idx_memory_signal_routes_route_uuid ON memory_signal_routes(route_uuid);
CREATE INDEX IF NOT EXISTS idx_memory_signal_routes_annotation_uuid ON memory_signal_routes(annotation_uuid);
CREATE INDEX IF NOT EXISTS idx_memory_signal_routes_message_uuid ON memory_signal_routes(message_uuid);
CREATE INDEX IF NOT EXISTS idx_memory_signal_routes_status_priority ON memory_signal_routes(status, priority);
CREATE INDEX IF NOT EXISTS idx_memory_candidates_status ON memory_candidates(status);
CREATE INDEX IF NOT EXISTS idx_memory_candidates_processing_run_uuid ON memory_candidates(processing_run_uuid);
CREATE INDEX IF NOT EXISTS idx_candidate_evidence_annotation_uuid ON candidate_evidence(annotation_uuid);
CREATE INDEX IF NOT EXISTS idx_candidate_evidence_route_uuid ON candidate_evidence(route_uuid);
CREATE INDEX IF NOT EXISTS idx_memories_memory_uuid ON memories(memory_uuid);
CREATE INDEX IF NOT EXISTS idx_memories_canonical_key ON memories(canonical_key);
CREATE UNIQUE INDEX IF NOT EXISTS idx_memories_scope_canonical_key
    ON memories(scope_type, COALESCE(scope_uuid, ''), canonical_key);
CREATE INDEX IF NOT EXISTS idx_memories_status ON memories(status);
CREATE INDEX IF NOT EXISTS idx_memory_versions_memory_uuid ON memory_versions(memory_uuid);
CREATE INDEX IF NOT EXISTS idx_memory_versions_status ON memory_versions(status);
CREATE INDEX IF NOT EXISTS idx_memory_blocks_canonical_key ON memory_blocks(canonical_key);
CREATE INDEX IF NOT EXISTS idx_memory_context_attachments_message_uuid ON memory_context_attachments(input_message_uuid);
CREATE INDEX IF NOT EXISTS idx_retrieval_runs_session_uuid ON retrieval_runs(session_uuid);
CREATE INDEX IF NOT EXISTS idx_retrieval_candidates_run_uuid ON retrieval_candidates(retrieval_run_uuid);
CREATE INDEX IF NOT EXISTS idx_import_runs_status ON import_runs(status);
CREATE INDEX IF NOT EXISTS idx_import_records_run_status ON import_records(import_run_uuid, status);
CREATE INDEX IF NOT EXISTS idx_import_batches_run_status ON import_commit_batches(import_run_uuid, status);
CREATE INDEX IF NOT EXISTS idx_privacy_requests_status ON privacy_requests(status);
CREATE INDEX IF NOT EXISTS idx_model_profiles_role ON model_profiles(role);
CREATE INDEX IF NOT EXISTS idx_jobs_status_priority_run_after ON jobs(status, priority DESC, run_after ASC, created_at ASC)
    WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS idx_jobs_locked_at ON jobs(status, locked_at)
    WHERE status = 'running';
CREATE INDEX IF NOT EXISTS idx_graph_outbox_status_priority ON graph_projection_outbox(status, priority DESC, run_after ASC, created_at ASC)
    WHERE status IN ('pending', 'retry');
CREATE INDEX IF NOT EXISTS idx_embedding_outbox_status_priority ON embedding_outbox(status, priority DESC, run_after ASC, created_at ASC)
    WHERE status IN ('pending', 'retry');
CREATE INDEX IF NOT EXISTS idx_memory_processing_outbox_status_priority ON memory_processing_outbox(status, priority DESC, run_after ASC, created_at ASC)
    WHERE status IN ('pending', 'retry');
CREATE INDEX IF NOT EXISTS idx_privacy_erasure_outbox_status_priority ON privacy_erasure_outbox(status, priority DESC, run_after ASC, created_at ASC)
    WHERE status IN ('pending', 'retry');
CREATE INDEX IF NOT EXISTS idx_graph_projection_state_source ON graph_projection_state(source_type, source_uuid, status);
CREATE INDEX IF NOT EXISTS idx_session_events_payload_gin ON session_events USING GIN(payload_jsonb);
CREATE INDEX IF NOT EXISTS idx_messages_raw_payload_gin ON messages USING GIN(raw_payload_jsonb);
CREATE INDEX IF NOT EXISTS idx_memory_candidates_object_gin ON memory_candidates USING GIN(object_jsonb);
