CREATE TABLE IF NOT EXISTS retrieval_runs (
    retrieval_run_uuid TEXT PRIMARY KEY,
    session_uuid TEXT NOT NULL,
    project_uuid TEXT,
    input_message_uuid TEXT NOT NULL,
    retrieval_mode TEXT NOT NULL,
    original_query TEXT NOT NULL,
    token_budget INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    planner_version TEXT NOT NULL,
    config_snapshot_ijson TEXT NOT NULL,
    abstention_status TEXT,
    abstention_reason TEXT,
    started_at TEXT,
    completed_at TEXT,
    latency_ms INTEGER,
    created_at TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY(session_uuid) REFERENCES sessions(session_uuid),
    FOREIGN KEY(input_message_uuid) REFERENCES messages(message_uuid)
);

CREATE TABLE IF NOT EXISTS retrieval_queries (
    retrieval_query_uuid TEXT PRIMARY KEY,
    retrieval_run_uuid TEXT NOT NULL,
    query_index INTEGER NOT NULL,
    query_type TEXT NOT NULL,
    query_text TEXT NOT NULL,
    weight REAL NOT NULL DEFAULT 1.0,
    filters_ijson TEXT NOT NULL,
    created_at TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY(retrieval_run_uuid) REFERENCES retrieval_runs(retrieval_run_uuid)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_retrieval_queries_order
ON retrieval_queries(retrieval_run_uuid, query_index);

CREATE TABLE IF NOT EXISTS retrieval_candidates (
    retrieval_candidate_uuid TEXT PRIMARY KEY,
    retrieval_run_uuid TEXT NOT NULL,
    retrieval_query_uuid TEXT,
    memory_uuid TEXT NOT NULL,
    memory_version_uuid TEXT NOT NULL,
    generator_type TEXT NOT NULL,
    generator_rank INTEGER,
    lexical_score REAL,
    semantic_score REAL,
    graph_score REAL,
    temporal_score REAL,
    authority_score REAL,
    confidence_score REAL,
    conflict_penalty REAL DEFAULT 0,
    fused_score REAL,
    rerank_score REAL,
    final_score REAL,
    decision TEXT NOT NULL DEFAULT 'candidate',
    exclusion_reasons_ijson TEXT,
    score_trace_ijson TEXT,
    created_at TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY(retrieval_run_uuid) REFERENCES retrieval_runs(retrieval_run_uuid),
    FOREIGN KEY(memory_uuid) REFERENCES memories(memory_uuid),
    FOREIGN KEY(memory_version_uuid) REFERENCES memory_versions(memory_version_uuid)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_retrieval_candidates_idempotency
ON retrieval_candidates(retrieval_run_uuid, memory_version_uuid, generator_type);

CREATE TABLE IF NOT EXISTS memory_version_embeddings (
    memory_version_uuid TEXT NOT NULL,
    embedding_model TEXT NOT NULL,
    embedding_dimension INTEGER NOT NULL,
    embedding_version TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    embedding_ijson TEXT NOT NULL,
    created_at TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY(memory_version_uuid, embedding_model, embedding_version),
    FOREIGN KEY(memory_version_uuid) REFERENCES memory_versions(memory_version_uuid)
);

CREATE VIRTUAL TABLE IF NOT EXISTS memory_version_fts USING fts5(
    memory_version_uuid UNINDEXED,
    memory_uuid UNINDEXED,
    canonical_key,
    memory_type,
    scope_type,
    scope_uuid UNINDEXED,
    subject_predicate,
    normalized_text,
    index_text
);

CREATE TABLE IF NOT EXISTS preflight_events (
    preflight_event_uuid TEXT PRIMARY KEY,
    session_uuid TEXT,
    input_message_uuid TEXT,
    attachment_uuid TEXT,
    retrieval_run_uuid TEXT,
    event_type TEXT NOT NULL,
    payload_ijson TEXT NOT NULL,
    created_at TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_preflight_events_dedupe
ON preflight_events(event_type, input_message_uuid, attachment_uuid)
WHERE input_message_uuid IS NOT NULL AND attachment_uuid IS NOT NULL;

CREATE TABLE IF NOT EXISTS assistant_response_links (
    response_link_uuid TEXT PRIMARY KEY,
    input_message_uuid TEXT NOT NULL,
    assistant_message_uuid TEXT NOT NULL,
    attachment_uuid TEXT,
    provider_response_id TEXT,
    content_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY(input_message_uuid) REFERENCES messages(message_uuid),
    FOREIGN KEY(assistant_message_uuid) REFERENCES messages(message_uuid)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_assistant_response_dedupe
ON assistant_response_links(input_message_uuid, COALESCE(provider_response_id, content_hash));
