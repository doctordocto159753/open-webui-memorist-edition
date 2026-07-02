ALTER TABLE text_units ADD COLUMN unit_uuid TEXT;
ALTER TABLE text_units ADD COLUMN char_start INTEGER;
ALTER TABLE text_units ADD COLUMN char_end INTEGER;
ALTER TABLE text_units ADD COLUMN language_hint TEXT;
ALTER TABLE text_units ADD COLUMN segmentation_confidence TEXT;
ALTER TABLE text_units ADD COLUMN segmentation_notes TEXT;

UPDATE text_units
SET unit_uuid = text_unit_uuid,
    char_start = start_char,
    char_end = end_char,
    language_hint = language_code,
    segmentation_confidence = 'medium'
WHERE unit_uuid IS NULL;

CREATE UNIQUE INDEX IF NOT EXISTS idx_text_units_unit_uuid
ON text_units(unit_uuid)
WHERE unit_uuid IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_text_units_message_type
ON text_units(message_uuid, unit_type, unit_index);

CREATE TABLE IF NOT EXISTS jakobson_analysis_runs (
    analysis_run_uuid TEXT PRIMARY KEY,
    workspace_uuid TEXT,
    project_uuid TEXT,
    session_uuid TEXT NOT NULL,
    message_uuid TEXT NOT NULL,
    prompt_id TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    model_profile_uuid TEXT,
    provider_type TEXT NOT NULL,
    model_name TEXT,
    input_hash TEXT NOT NULL,
    output_hash TEXT,
    status TEXT NOT NULL,
    warnings_ijson TEXT NOT NULL,
    raw_output_ijson TEXT,
    created_at TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY(workspace_uuid) REFERENCES workspaces(workspace_uuid),
    FOREIGN KEY(project_uuid) REFERENCES projects(project_uuid),
    FOREIGN KEY(session_uuid) REFERENCES sessions(session_uuid),
    FOREIGN KEY(message_uuid) REFERENCES messages(message_uuid)
);

CREATE INDEX IF NOT EXISTS idx_jakobson_runs_message
ON jakobson_analysis_runs(message_uuid, created_at);

CREATE INDEX IF NOT EXISTS idx_jakobson_runs_prompt
ON jakobson_analysis_runs(prompt_id, prompt_version, created_at);

CREATE TABLE IF NOT EXISTS jakobson_sentence_annotations (
    annotation_uuid TEXT PRIMARY KEY,
    analysis_run_uuid TEXT NOT NULL,
    message_uuid TEXT NOT NULL,
    unit_uuid TEXT NOT NULL,
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
    secondary_functions_ijson TEXT NOT NULL,
    function_reason TEXT,
    notes TEXT,
    raw_sentence_output_ijson TEXT NOT NULL,
    created_at TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY(analysis_run_uuid) REFERENCES jakobson_analysis_runs(analysis_run_uuid),
    FOREIGN KEY(message_uuid) REFERENCES messages(message_uuid),
    FOREIGN KEY(unit_uuid) REFERENCES text_units(text_unit_uuid)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_jakobson_annotation_unit_run
ON jakobson_sentence_annotations(analysis_run_uuid, unit_uuid);

CREATE INDEX IF NOT EXISTS idx_jakobson_annotations_message
ON jakobson_sentence_annotations(message_uuid);

CREATE INDEX IF NOT EXISTS idx_jakobson_annotations_unit
ON jakobson_sentence_annotations(unit_uuid);

CREATE INDEX IF NOT EXISTS idx_jakobson_annotations_dominant
ON jakobson_sentence_annotations(dominant_function);

CREATE INDEX IF NOT EXISTS idx_jakobson_annotations_receiver
ON jakobson_sentence_annotations(receiver_value);

CREATE INDEX IF NOT EXISTS idx_jakobson_annotations_context
ON jakobson_sentence_annotations(context_value);

CREATE INDEX IF NOT EXISTS idx_jakobson_annotations_code
ON jakobson_sentence_annotations(code_value);

CREATE INDEX IF NOT EXISTS idx_jakobson_annotations_contact
ON jakobson_sentence_annotations(contact_channel_value);

CREATE INDEX IF NOT EXISTS idx_jakobson_annotations_sentence_hash
ON jakobson_sentence_annotations(sentence_hash);

CREATE INDEX IF NOT EXISTS idx_jakobson_annotations_run
ON jakobson_sentence_annotations(analysis_run_uuid);

CREATE TABLE IF NOT EXISTS memory_signal_routes (
    route_uuid TEXT PRIMARY KEY,
    annotation_uuid TEXT NOT NULL,
    message_uuid TEXT NOT NULL,
    unit_uuid TEXT NOT NULL,
    dominant_function TEXT NOT NULL,
    secondary_functions_ijson TEXT NOT NULL,
    route_type TEXT NOT NULL,
    extractor_id TEXT NOT NULL,
    priority INTEGER NOT NULL,
    confidence TEXT NOT NULL,
    reason TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY(annotation_uuid) REFERENCES jakobson_sentence_annotations(annotation_uuid),
    FOREIGN KEY(message_uuid) REFERENCES messages(message_uuid),
    FOREIGN KEY(unit_uuid) REFERENCES text_units(text_unit_uuid)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_signal_routes_idempotency
ON memory_signal_routes(annotation_uuid, route_type, extractor_id);

CREATE INDEX IF NOT EXISTS idx_memory_signal_routes_annotation
ON memory_signal_routes(annotation_uuid);

CREATE INDEX IF NOT EXISTS idx_memory_signal_routes_message
ON memory_signal_routes(message_uuid);

CREATE INDEX IF NOT EXISTS idx_memory_signal_routes_unit
ON memory_signal_routes(unit_uuid);

CREATE INDEX IF NOT EXISTS idx_memory_signal_routes_dominant
ON memory_signal_routes(dominant_function);

CREATE INDEX IF NOT EXISTS idx_memory_signal_routes_type
ON memory_signal_routes(route_type, status, priority);

ALTER TABLE candidate_evidence ADD COLUMN annotation_uuid TEXT;
ALTER TABLE candidate_evidence ADD COLUMN route_uuid TEXT;

CREATE INDEX IF NOT EXISTS idx_candidate_evidence_annotation
ON candidate_evidence(annotation_uuid)
WHERE annotation_uuid IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_candidate_evidence_route
ON candidate_evidence(route_uuid)
WHERE route_uuid IS NOT NULL;
