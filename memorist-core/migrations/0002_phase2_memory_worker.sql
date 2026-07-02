CREATE TABLE IF NOT EXISTS memory_processing_runs (
    processing_run_uuid TEXT PRIMARY KEY,
    session_uuid TEXT NOT NULL,
    message_uuid TEXT NOT NULL,
    pipeline_version TEXT NOT NULL,
    prompt_bundle_version TEXT,
    model_profile_uuid TEXT,
    input_content_hash TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    started_at TEXT,
    completed_at TEXT,
    latency_ms INTEGER,
    token_count_input INTEGER DEFAULT 0,
    token_count_output INTEGER DEFAULT 0,
    raw_output_ijson TEXT,
    validation_errors_ijson TEXT,
    error_text TEXT,
    created_at TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY(session_uuid) REFERENCES sessions(session_uuid),
    FOREIGN KEY(message_uuid) REFERENCES messages(message_uuid)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_processing_run_idempotency
ON memory_processing_runs(message_uuid, pipeline_version, input_content_hash);

CREATE TABLE IF NOT EXISTS text_units (
    text_unit_uuid TEXT PRIMARY KEY,
    message_uuid TEXT NOT NULL,
    session_uuid TEXT NOT NULL,
    unit_index INTEGER NOT NULL,
    unit_type TEXT NOT NULL,
    text TEXT NOT NULL,
    start_char INTEGER NOT NULL,
    end_char INTEGER NOT NULL,
    language_code TEXT,
    speaker_role TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY(message_uuid) REFERENCES messages(message_uuid),
    FOREIGN KEY(session_uuid) REFERENCES sessions(session_uuid)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_text_units_order
ON text_units(message_uuid, unit_index);

CREATE TABLE IF NOT EXISTS memory_gate_decisions (
    gate_decision_uuid TEXT PRIMARY KEY,
    text_unit_uuid TEXT NOT NULL,
    processing_run_uuid TEXT NOT NULL,
    decision TEXT NOT NULL,
    reason_codes_ijson TEXT NOT NULL,
    salience_score REAL NOT NULL,
    persistence_score REAL NOT NULL,
    actionability_score REAL NOT NULL,
    sensitivity_score REAL NOT NULL,
    novelty_score REAL NOT NULL,
    requires_high_confidence_pass INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY(text_unit_uuid) REFERENCES text_units(text_unit_uuid),
    FOREIGN KEY(processing_run_uuid) REFERENCES memory_processing_runs(processing_run_uuid)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_gate_decision_idempotency
ON memory_gate_decisions(text_unit_uuid, processing_run_uuid);

CREATE TABLE IF NOT EXISTS linguistic_analyses (
    analysis_uuid TEXT PRIMARY KEY,
    text_unit_uuid TEXT NOT NULL,
    processing_run_uuid TEXT NOT NULL,
    analysis_schema_version TEXT NOT NULL,
    speech_acts_ijson TEXT NOT NULL,
    jakobson_functions_ijson TEXT NOT NULL,
    conceptual_nuclei_ijson TEXT NOT NULL,
    entity_mentions_ijson TEXT NOT NULL,
    temporal_expressions_ijson TEXT NOT NULL,
    modality_ijson TEXT NOT NULL,
    memory_signals_ijson TEXT NOT NULL,
    abstention_ijson TEXT NOT NULL,
    raw_output_ijson TEXT,
    validation_errors_ijson TEXT,
    created_at TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY(text_unit_uuid) REFERENCES text_units(text_unit_uuid),
    FOREIGN KEY(processing_run_uuid) REFERENCES memory_processing_runs(processing_run_uuid)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_linguistic_analysis_idempotency
ON linguistic_analyses(text_unit_uuid, processing_run_uuid);

CREATE TABLE IF NOT EXISTS memory_candidates (
    candidate_uuid TEXT PRIMARY KEY,
    processing_run_uuid TEXT NOT NULL,
    text_unit_uuid TEXT NOT NULL,
    candidate_type TEXT NOT NULL,
    subject_key TEXT,
    predicate TEXT,
    object_ijson TEXT,
    normalized_text TEXT NOT NULL,
    source_authority TEXT NOT NULL,
    explicitness TEXT NOT NULL,
    confidence REAL NOT NULL,
    importance REAL NOT NULL,
    valid_from TEXT,
    valid_until TEXT,
    temporal_precision TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    sensitivity_class TEXT NOT NULL DEFAULT 'normal',
    extraction_metadata_ijson TEXT,
    rejection_reason_codes_ijson TEXT,
    created_at TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY(processing_run_uuid) REFERENCES memory_processing_runs(processing_run_uuid),
    FOREIGN KEY(text_unit_uuid) REFERENCES text_units(text_unit_uuid)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_candidate_idempotency
ON memory_candidates(processing_run_uuid, text_unit_uuid, candidate_type, normalized_text);

CREATE TABLE IF NOT EXISTS candidate_evidence (
    evidence_uuid TEXT PRIMARY KEY,
    candidate_uuid TEXT NOT NULL,
    message_uuid TEXT NOT NULL,
    text_unit_uuid TEXT NOT NULL,
    evidence_text TEXT NOT NULL,
    start_char INTEGER NOT NULL,
    end_char INTEGER NOT NULL,
    evidence_role TEXT NOT NULL,
    support_type TEXT NOT NULL,
    created_at TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY(candidate_uuid) REFERENCES memory_candidates(candidate_uuid),
    FOREIGN KEY(message_uuid) REFERENCES messages(message_uuid),
    FOREIGN KEY(text_unit_uuid) REFERENCES text_units(text_unit_uuid)
);

CREATE TABLE IF NOT EXISTS memories (
    memory_uuid TEXT PRIMARY KEY,
    canonical_key TEXT NOT NULL,
    memory_type TEXT NOT NULL,
    scope_type TEXT NOT NULL,
    scope_uuid TEXT,
    current_version_uuid TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    sensitivity_class TEXT NOT NULL DEFAULT 'normal',
    created_at TEXT NOT NULL,
    updated_at TEXT,
    schema_version INTEGER NOT NULL DEFAULT 1
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_memories_canonical_scope
ON memories(scope_type, scope_uuid, canonical_key);

CREATE TABLE IF NOT EXISTS memory_versions (
    memory_version_uuid TEXT PRIMARY KEY,
    memory_uuid TEXT NOT NULL,
    version_number INTEGER NOT NULL,
    normalized_text TEXT NOT NULL,
    value_ijson TEXT,
    confidence REAL NOT NULL,
    importance REAL NOT NULL,
    valid_from TEXT,
    valid_until TEXT,
    transaction_from TEXT NOT NULL,
    transaction_until TEXT,
    source_candidate_uuid TEXT,
    change_operation TEXT NOT NULL,
    change_reason_ijson TEXT,
    created_at TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY(memory_uuid) REFERENCES memories(memory_uuid),
    FOREIGN KEY(source_candidate_uuid) REFERENCES memory_candidates(candidate_uuid)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_version_number
ON memory_versions(memory_uuid, version_number);

CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_versions_source_candidate
ON memory_versions(source_candidate_uuid, change_operation)
WHERE source_candidate_uuid IS NOT NULL;

CREATE TABLE IF NOT EXISTS memory_evidence_links (
    memory_evidence_link_uuid TEXT PRIMARY KEY,
    memory_uuid TEXT NOT NULL,
    memory_version_uuid TEXT NOT NULL,
    candidate_uuid TEXT NOT NULL,
    evidence_uuid TEXT NOT NULL,
    link_type TEXT NOT NULL DEFAULT 'supports',
    created_at TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY(memory_uuid) REFERENCES memories(memory_uuid),
    FOREIGN KEY(memory_version_uuid) REFERENCES memory_versions(memory_version_uuid),
    FOREIGN KEY(candidate_uuid) REFERENCES memory_candidates(candidate_uuid),
    FOREIGN KEY(evidence_uuid) REFERENCES candidate_evidence(evidence_uuid)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_evidence_link_idempotency
ON memory_evidence_links(memory_version_uuid, evidence_uuid, link_type);

CREATE TABLE IF NOT EXISTS memory_relations (
    relation_uuid TEXT PRIMARY KEY,
    source_memory_uuid TEXT NOT NULL,
    target_memory_uuid TEXT NOT NULL,
    relation_type TEXT NOT NULL,
    source_memory_version_uuid TEXT,
    target_memory_version_uuid TEXT,
    created_at TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY(source_memory_uuid) REFERENCES memories(memory_uuid),
    FOREIGN KEY(target_memory_uuid) REFERENCES memories(memory_uuid)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_relation_idempotency
ON memory_relations(source_memory_uuid, target_memory_uuid, relation_type);

CREATE TABLE IF NOT EXISTS memory_consolidation_decisions (
    consolidation_decision_uuid TEXT PRIMARY KEY,
    candidate_uuid TEXT NOT NULL,
    operation TEXT NOT NULL,
    resolver_version TEXT NOT NULL,
    reason_codes_ijson TEXT NOT NULL,
    target_memory_uuid TEXT,
    target_memory_version_uuid TEXT,
    created_at TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY(candidate_uuid) REFERENCES memory_candidates(candidate_uuid),
    FOREIGN KEY(target_memory_uuid) REFERENCES memories(memory_uuid),
    FOREIGN KEY(target_memory_version_uuid) REFERENCES memory_versions(memory_version_uuid)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_consolidation_decision_candidate
ON memory_consolidation_decisions(candidate_uuid);

CREATE TABLE IF NOT EXISTS graph_projection_outbox (
    outbox_uuid TEXT PRIMARY KEY,
    aggregate_type TEXT NOT NULL,
    aggregate_uuid TEXT NOT NULL,
    operation TEXT NOT NULL,
    payload_ijson TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    created_at TEXT NOT NULL,
    processed_at TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_graph_outbox_idempotency
ON graph_projection_outbox(aggregate_type, aggregate_uuid, operation);
