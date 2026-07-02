ALTER TABLE memory_blocks ADD COLUMN current_version_uuid TEXT;
ALTER TABLE memory_blocks ADD COLUMN builder_policy_ijson TEXT;
ALTER TABLE memory_blocks ADD COLUMN optimistic_lock_version INTEGER NOT NULL DEFAULT 0;
ALTER TABLE memory_blocks ADD COLUMN source_snapshot_hash TEXT;
ALTER TABLE memory_blocks ADD COLUMN build_status TEXT NOT NULL DEFAULT 'stale';

ALTER TABLE memory_context_attachments ADD COLUMN status TEXT NOT NULL DEFAULT 'active';
ALTER TABLE memory_context_attachments ADD COLUMN stale_reason TEXT;

CREATE TABLE IF NOT EXISTS memory_block_build_runs (
    block_build_run_uuid TEXT PRIMARY KEY,
    block_uuid TEXT NOT NULL,
    builder_version TEXT NOT NULL,
    source_snapshot_hash TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    trigger_type TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    raw_output_ijson TEXT,
    validation_errors_ijson TEXT,
    error_text TEXT,
    created_at TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY(block_uuid) REFERENCES memory_blocks(block_uuid)
);

CREATE TABLE IF NOT EXISTS memory_block_versions (
    block_version_uuid TEXT PRIMARY KEY,
    block_uuid TEXT NOT NULL,
    version_number INTEGER NOT NULL,
    value TEXT NOT NULL,
    structured_value_ijson TEXT,
    char_count INTEGER NOT NULL,
    token_count INTEGER NOT NULL,
    source_snapshot_hash TEXT NOT NULL,
    block_build_run_uuid TEXT,
    change_reason TEXT,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY(block_uuid) REFERENCES memory_blocks(block_uuid),
    FOREIGN KEY(block_build_run_uuid) REFERENCES memory_block_build_runs(block_build_run_uuid)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_block_versions_number
ON memory_block_versions(block_uuid, version_number);

CREATE TABLE IF NOT EXISTS memory_block_sources (
    block_version_uuid TEXT NOT NULL,
    memory_uuid TEXT NOT NULL,
    memory_version_uuid TEXT NOT NULL,
    source_role TEXT NOT NULL,
    inclusion_reason TEXT,
    PRIMARY KEY(block_version_uuid, memory_version_uuid, source_role),
    FOREIGN KEY(block_version_uuid) REFERENCES memory_block_versions(block_version_uuid),
    FOREIGN KEY(memory_uuid) REFERENCES memories(memory_uuid),
    FOREIGN KEY(memory_version_uuid) REFERENCES memory_versions(memory_version_uuid)
);

CREATE TABLE IF NOT EXISTS memory_delivery_events (
    delivery_event_uuid TEXT PRIMARY KEY,
    response_message_uuid TEXT,
    input_message_uuid TEXT NOT NULL,
    attachment_uuid TEXT,
    retrieval_run_uuid TEXT,
    memory_uuid TEXT NOT NULL,
    memory_version_uuid TEXT NOT NULL,
    delivery_stage TEXT NOT NULL,
    rank_at_stage INTEGER,
    score_at_stage REAL,
    created_at TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_delivery_attachment
ON memory_delivery_events(attachment_uuid, delivery_stage);

CREATE INDEX IF NOT EXISTS idx_delivery_memory
ON memory_delivery_events(memory_uuid, memory_version_uuid);

CREATE TABLE IF NOT EXISTS response_memory_attributions (
    attribution_uuid TEXT PRIMARY KEY,
    response_message_uuid TEXT NOT NULL,
    memory_uuid TEXT NOT NULL,
    memory_version_uuid TEXT NOT NULL,
    attribution_type TEXT NOT NULL,
    attribution_status TEXT NOT NULL,
    response_span_text TEXT,
    response_start_char INTEGER,
    response_end_char INTEGER,
    confidence REAL,
    method TEXT NOT NULL,
    evidence_ijson TEXT,
    created_at TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS memory_feedback (
    feedback_uuid TEXT PRIMARY KEY,
    memory_uuid TEXT,
    memory_version_uuid TEXT,
    attachment_uuid TEXT,
    response_message_uuid TEXT,
    feedback_type TEXT NOT NULL,
    rating INTEGER,
    comment TEXT,
    actor_type TEXT NOT NULL,
    actor_uuid TEXT,
    created_at TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS memory_change_requests (
    change_request_uuid TEXT PRIMARY KEY,
    memory_uuid TEXT,
    requested_operation TEXT NOT NULL,
    proposed_value_ijson TEXT,
    reason TEXT,
    actor_type TEXT NOT NULL,
    actor_uuid TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    impact_preview_ijson TEXT,
    applied_memory_version_uuid TEXT,
    created_at TEXT NOT NULL,
    reviewed_at TEXT,
    applied_at TEXT,
    schema_version INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS memory_redirects (
    old_memory_uuid TEXT PRIMARY KEY,
    new_memory_uuid TEXT NOT NULL,
    reason TEXT,
    created_at TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS privacy_requests (
    privacy_request_uuid TEXT PRIMARY KEY,
    request_type TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_uuid TEXT,
    requested_scope_ijson TEXT NOT NULL,
    actor_type TEXT NOT NULL,
    actor_uuid TEXT,
    status TEXT NOT NULL DEFAULT 'preview',
    confirmation_token_hash TEXT,
    confirmation_expires_at TEXT,
    policy_decision_ijson TEXT,
    created_at TEXT NOT NULL,
    confirmed_at TEXT,
    completed_at TEXT,
    schema_version INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS privacy_request_items (
    privacy_request_item_uuid TEXT PRIMARY KEY,
    privacy_request_uuid TEXT NOT NULL,
    storage_type TEXT NOT NULL,
    record_type TEXT NOT NULL,
    record_uuid TEXT,
    action TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    last_error TEXT,
    processed_at TEXT,
    schema_version INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY(privacy_request_uuid) REFERENCES privacy_requests(privacy_request_uuid)
);

CREATE TABLE IF NOT EXISTS erasure_receipts (
    erasure_receipt_uuid TEXT PRIMARY KEY,
    privacy_request_uuid TEXT NOT NULL,
    request_fingerprint TEXT NOT NULL,
    erased_record_counts_ijson TEXT NOT NULL,
    retained_record_counts_ijson TEXT NOT NULL,
    exceptions_ijson TEXT NOT NULL,
    verification_ijson TEXT NOT NULL,
    completed_at TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1
);
