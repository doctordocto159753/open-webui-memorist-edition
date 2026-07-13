CREATE TABLE IF NOT EXISTS memorist_policy_defaults (
    policy_default_uuid TEXT PRIMARY KEY,
    scope_type TEXT NOT NULL CHECK(scope_type IN ('system', 'user', 'chat')),
    scope_uuid TEXT NOT NULL,
    workspace_uuid TEXT,
    turn_policy TEXT NOT NULL CHECK(turn_policy IN ('full', 'no_recall', 'private')),
    attachment_review INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1,
    UNIQUE(scope_type, scope_uuid, workspace_uuid)
);

CREATE TABLE IF NOT EXISTS memorist_turn_contracts (
    turn_contract_uuid TEXT PRIMARY KEY,
    input_message_uuid TEXT NOT NULL UNIQUE,
    session_uuid TEXT NOT NULL,
    workspace_uuid TEXT,
    user_uuid TEXT,
    chat_uuid TEXT,
    turn_policy TEXT NOT NULL CHECK(turn_policy IN ('full', 'no_recall')),
    capture_enabled INTEGER NOT NULL,
    recall_enabled INTEGER NOT NULL,
    attachment_enabled INTEGER NOT NULL,
    attachment_review INTEGER NOT NULL DEFAULT 0,
    policy_source TEXT NOT NULL,
    runtime_profile TEXT NOT NULL,
    created_at TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY(input_message_uuid) REFERENCES messages(message_uuid),
    FOREIGN KEY(session_uuid) REFERENCES sessions(session_uuid)
);

CREATE TABLE IF NOT EXISTS memorist_policy_audit_events (
    audit_event_uuid TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    input_message_uuid TEXT,
    session_uuid TEXT,
    workspace_uuid TEXT,
    user_uuid TEXT,
    attachment_uuid TEXT,
    turn_policy TEXT NOT NULL,
    runtime_profile TEXT NOT NULL,
    degraded_reason TEXT,
    detail_ijson TEXT NOT NULL,
    created_at TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS memorist_attachment_lifecycle_events (
    lifecycle_event_uuid TEXT PRIMARY KEY,
    attachment_uuid TEXT NOT NULL,
    lifecycle_status TEXT NOT NULL CHECK(lifecycle_status IN (
        'prepared', 'approved', 'delivered', 'suppressed',
        'cancelled_before_send', 'user_rejected', 'used_for_response'
    )),
    idempotency_key TEXT NOT NULL,
    user_uuid TEXT,
    workspace_uuid TEXT,
    response_message_uuid TEXT,
    detail_ijson TEXT NOT NULL,
    created_at TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1,
    UNIQUE(attachment_uuid, lifecycle_status, idempotency_key),
    FOREIGN KEY(attachment_uuid) REFERENCES memory_context_attachments(attachment_uuid)
);

CREATE TABLE IF NOT EXISTS memorist_retrieval_sources (
    retrieval_source_uuid TEXT PRIMARY KEY,
    retrieval_run_uuid TEXT NOT NULL,
    attachment_uuid TEXT,
    source_type TEXT NOT NULL,
    source_uuid TEXT NOT NULL,
    memory_uuid TEXT,
    memory_version_uuid TEXT,
    workspace_uuid TEXT,
    provenance_ijson TEXT NOT NULL,
    created_at TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1,
    UNIQUE(retrieval_run_uuid, source_type, source_uuid)
);

CREATE TABLE IF NOT EXISTS memorist_regenerations (
    regeneration_uuid TEXT PRIMARY KEY,
    original_input_message_uuid TEXT NOT NULL,
    original_attachment_uuid TEXT,
    regenerated_assistant_message_uuid TEXT,
    user_uuid TEXT,
    workspace_uuid TEXT,
    turn_policy TEXT NOT NULL DEFAULT 'no_recall' CHECK(turn_policy = 'no_recall'),
    idempotency_key TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'requested',
    created_at TEXT NOT NULL,
    completed_at TEXT,
    schema_version INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY(original_input_message_uuid) REFERENCES messages(message_uuid)
);

ALTER TABLE memory_context_attachments ADD COLUMN owner_user_uuid TEXT;
ALTER TABLE memory_context_attachments ADD COLUMN workspace_uuid TEXT;
ALTER TABLE memory_context_attachments ADD COLUMN lifecycle_status TEXT NOT NULL DEFAULT 'prepared';
ALTER TABLE memory_context_attachments ADD COLUMN attachment_review INTEGER NOT NULL DEFAULT 0;
ALTER TABLE memory_context_attachments ADD COLUMN approved_at TEXT;
ALTER TABLE memory_context_attachments ADD COLUMN delivered_at TEXT;
ALTER TABLE memory_context_attachments ADD COLUMN suppressed_at TEXT;
ALTER TABLE memory_context_attachments ADD COLUMN cancelled_at TEXT;
ALTER TABLE memory_context_attachments ADD COLUMN generation INTEGER NOT NULL DEFAULT 1;
ALTER TABLE memory_context_attachments ADD COLUMN expires_at TEXT;

ALTER TABLE retrieval_runs ADD COLUMN turn_policy TEXT NOT NULL DEFAULT 'full';
ALTER TABLE retrieval_runs ADD COLUMN graph_status TEXT;
ALTER TABLE retrieval_runs ADD COLUMN degraded_reason TEXT;

CREATE INDEX IF NOT EXISTS idx_memorist_policy_defaults_resolution
ON memorist_policy_defaults(scope_type, scope_uuid, workspace_uuid);

CREATE UNIQUE INDEX IF NOT EXISTS idx_memorist_policy_defaults_scope_unique
ON memorist_policy_defaults(scope_type, scope_uuid, COALESCE(workspace_uuid, ''));

CREATE INDEX IF NOT EXISTS idx_memorist_turn_contract_scope
ON memorist_turn_contracts(workspace_uuid, user_uuid, chat_uuid);

CREATE INDEX IF NOT EXISTS idx_memorist_attachment_scope
ON memory_context_attachments(workspace_uuid, owner_user_uuid, lifecycle_status);

CREATE INDEX IF NOT EXISTS idx_memorist_attachment_lifecycle
ON memorist_attachment_lifecycle_events(attachment_uuid, created_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_memorist_attachment_single_lifecycle_status
ON memorist_attachment_lifecycle_events(attachment_uuid, lifecycle_status);
