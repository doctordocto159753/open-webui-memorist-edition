CREATE TABLE IF NOT EXISTS memorist_policy_defaults (
    policy_default_uuid TEXT PRIMARY KEY,
    scope_type TEXT NOT NULL CHECK(scope_type IN ('system', 'user', 'chat')),
    scope_uuid TEXT NOT NULL,
    workspace_uuid TEXT,
    turn_policy TEXT NOT NULL CHECK(turn_policy IN ('full', 'no_recall', 'private')),
    attachment_review BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    schema_version INTEGER NOT NULL DEFAULT 1,
    UNIQUE(scope_type, scope_uuid, workspace_uuid)
);

CREATE TABLE IF NOT EXISTS memorist_turn_contracts (
    turn_contract_uuid TEXT PRIMARY KEY,
    input_message_uuid TEXT NOT NULL UNIQUE REFERENCES messages(message_uuid),
    session_uuid TEXT NOT NULL REFERENCES sessions(session_uuid),
    workspace_uuid TEXT,
    user_uuid TEXT,
    chat_uuid TEXT,
    turn_policy TEXT NOT NULL CHECK(turn_policy IN ('full', 'no_recall')),
    capture_enabled BOOLEAN NOT NULL,
    recall_enabled BOOLEAN NOT NULL,
    attachment_enabled BOOLEAN NOT NULL,
    attachment_review BOOLEAN NOT NULL DEFAULT false,
    policy_source TEXT NOT NULL,
    runtime_profile TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    schema_version INTEGER NOT NULL DEFAULT 1
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
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    schema_version INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS memorist_attachment_lifecycle_events (
    lifecycle_event_uuid TEXT PRIMARY KEY,
    attachment_uuid TEXT NOT NULL REFERENCES memory_context_attachments(attachment_uuid),
    lifecycle_status TEXT NOT NULL CHECK(lifecycle_status IN (
        'prepared', 'approved', 'delivered', 'suppressed',
        'cancelled_before_send', 'user_rejected', 'used_for_response'
    )),
    idempotency_key TEXT NOT NULL,
    user_uuid TEXT,
    workspace_uuid TEXT,
    response_message_uuid TEXT,
    detail_ijson TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    schema_version INTEGER NOT NULL DEFAULT 1,
    UNIQUE(attachment_uuid, lifecycle_status, idempotency_key)
);

CREATE TABLE IF NOT EXISTS memorist_retrieval_sources (
    retrieval_source_uuid TEXT PRIMARY KEY,
    retrieval_run_uuid TEXT NOT NULL REFERENCES retrieval_runs(retrieval_run_uuid),
    attachment_uuid TEXT,
    source_type TEXT NOT NULL,
    source_uuid TEXT NOT NULL,
    memory_uuid TEXT,
    memory_version_uuid TEXT,
    workspace_uuid TEXT,
    provenance_ijson TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    schema_version INTEGER NOT NULL DEFAULT 1,
    UNIQUE(retrieval_run_uuid, source_type, source_uuid)
);

CREATE TABLE IF NOT EXISTS memorist_regenerations (
    regeneration_uuid TEXT PRIMARY KEY,
    original_input_message_uuid TEXT NOT NULL REFERENCES messages(message_uuid),
    original_attachment_uuid TEXT,
    regenerated_assistant_message_uuid TEXT,
    user_uuid TEXT,
    workspace_uuid TEXT,
    turn_policy TEXT NOT NULL DEFAULT 'no_recall' CHECK(turn_policy = 'no_recall'),
    idempotency_key TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'requested',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ,
    schema_version INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS retrieval_queries (
    retrieval_query_uuid TEXT PRIMARY KEY,
    retrieval_run_uuid TEXT NOT NULL REFERENCES retrieval_runs(retrieval_run_uuid),
    query_index INTEGER NOT NULL,
    query_type TEXT NOT NULL,
    query_text TEXT NOT NULL,
    weight DOUBLE PRECISION NOT NULL DEFAULT 1.0,
    filters_ijson TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    schema_version INTEGER NOT NULL DEFAULT 1,
    UNIQUE(retrieval_run_uuid, query_index)
);

CREATE TABLE IF NOT EXISTS preflight_events (
    preflight_event_uuid TEXT PRIMARY KEY,
    session_uuid TEXT,
    input_message_uuid TEXT,
    attachment_uuid TEXT,
    retrieval_run_uuid TEXT,
    event_type TEXT NOT NULL,
    payload_ijson TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    schema_version INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS assistant_response_links (
    response_link_uuid TEXT PRIMARY KEY,
    input_message_uuid TEXT NOT NULL REFERENCES messages(message_uuid),
    assistant_message_uuid TEXT NOT NULL REFERENCES messages(message_uuid),
    attachment_uuid TEXT,
    provider_response_id TEXT,
    content_hash TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    schema_version INTEGER NOT NULL DEFAULT 1
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_assistant_response_dedupe_provider
ON assistant_response_links(input_message_uuid, provider_response_id)
WHERE provider_response_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_assistant_response_dedupe_hash
ON assistant_response_links(input_message_uuid, content_hash)
WHERE provider_response_id IS NULL;

ALTER TABLE memory_context_attachments ADD COLUMN IF NOT EXISTS project_uuid TEXT;
ALTER TABLE memory_context_attachments ADD COLUMN IF NOT EXISTS attachment_mode TEXT;
ALTER TABLE memory_context_attachments ADD COLUMN IF NOT EXISTS ijson_attachment TEXT;
ALTER TABLE memory_context_attachments ADD COLUMN IF NOT EXISTS rendered_attachment TEXT;
ALTER TABLE memory_context_attachments ADD COLUMN IF NOT EXISTS source_block_uuids_ijson TEXT;
ALTER TABLE memory_context_attachments ADD COLUMN IF NOT EXISTS source_memory_uuids_ijson TEXT;
ALTER TABLE memory_context_attachments ADD COLUMN IF NOT EXISTS source_fact_edge_uuids_ijson TEXT;
ALTER TABLE memory_context_attachments ADD COLUMN IF NOT EXISTS confidence DOUBLE PRECISION DEFAULT 0.5;
ALTER TABLE memory_context_attachments ADD COLUMN IF NOT EXISTS abstention_status TEXT;
ALTER TABLE memory_context_attachments ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'active';
ALTER TABLE memory_context_attachments ADD COLUMN IF NOT EXISTS stale_reason TEXT;
ALTER TABLE memory_context_attachments ADD COLUMN IF NOT EXISTS owner_user_uuid TEXT;
ALTER TABLE memory_context_attachments ADD COLUMN IF NOT EXISTS workspace_uuid TEXT;
ALTER TABLE memory_context_attachments ADD COLUMN IF NOT EXISTS lifecycle_status TEXT NOT NULL DEFAULT 'prepared';
ALTER TABLE memory_context_attachments ADD COLUMN IF NOT EXISTS attachment_review BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE memory_context_attachments ADD COLUMN IF NOT EXISTS approved_at TIMESTAMPTZ;
ALTER TABLE memory_context_attachments ADD COLUMN IF NOT EXISTS delivered_at TIMESTAMPTZ;
ALTER TABLE memory_context_attachments ADD COLUMN IF NOT EXISTS suppressed_at TIMESTAMPTZ;
ALTER TABLE memory_context_attachments ADD COLUMN IF NOT EXISTS cancelled_at TIMESTAMPTZ;
ALTER TABLE memory_context_attachments ADD COLUMN IF NOT EXISTS generation INTEGER NOT NULL DEFAULT 1;
ALTER TABLE memory_context_attachments ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ;
ALTER TABLE memory_context_attachments ALTER COLUMN attachment_jsonb DROP NOT NULL;
ALTER TABLE memory_context_attachments ALTER COLUMN mode DROP NOT NULL;

ALTER TABLE retrieval_runs ADD COLUMN IF NOT EXISTS config_snapshot_ijson TEXT;
ALTER TABLE retrieval_runs ADD COLUMN IF NOT EXISTS abstention_status TEXT;
ALTER TABLE retrieval_runs ADD COLUMN IF NOT EXISTS abstention_reason TEXT;
ALTER TABLE retrieval_runs ADD COLUMN IF NOT EXISTS completed_at TIMESTAMPTZ;
ALTER TABLE retrieval_runs ADD COLUMN IF NOT EXISTS turn_policy TEXT NOT NULL DEFAULT 'full';
ALTER TABLE retrieval_runs ADD COLUMN IF NOT EXISTS graph_status TEXT;
ALTER TABLE retrieval_runs ADD COLUMN IF NOT EXISTS degraded_reason TEXT;
ALTER TABLE retrieval_runs ALTER COLUMN config_snapshot_jsonb DROP NOT NULL;

ALTER TABLE retrieval_candidates ADD COLUMN IF NOT EXISTS retrieval_query_uuid TEXT;
ALTER TABLE retrieval_candidates ADD COLUMN IF NOT EXISTS temporal_score DOUBLE PRECISION;
ALTER TABLE retrieval_candidates ADD COLUMN IF NOT EXISTS authority_score DOUBLE PRECISION;
ALTER TABLE retrieval_candidates ADD COLUMN IF NOT EXISTS confidence_score DOUBLE PRECISION;
ALTER TABLE retrieval_candidates ADD COLUMN IF NOT EXISTS conflict_penalty DOUBLE PRECISION DEFAULT 0;
ALTER TABLE retrieval_candidates ADD COLUMN IF NOT EXISTS rerank_score DOUBLE PRECISION;
ALTER TABLE retrieval_candidates ADD COLUMN IF NOT EXISTS exclusion_reasons_ijson TEXT;
ALTER TABLE retrieval_candidates ADD COLUMN IF NOT EXISTS score_trace_ijson TEXT;

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
