ALTER TABLE memory_processing_runs ADD COLUMN IF NOT EXISTS model_profile_uuid TEXT REFERENCES model_profiles(model_profile_uuid);
ALTER TABLE memory_processing_runs ADD COLUMN IF NOT EXISTS provider_type TEXT;
ALTER TABLE memory_processing_runs ADD COLUMN IF NOT EXISTS input_hash TEXT;
ALTER TABLE memory_processing_runs ADD COLUMN IF NOT EXISTS output_hash TEXT;
ALTER TABLE memory_processing_runs ADD COLUMN IF NOT EXISTS completed_at TIMESTAMPTZ;
ALTER TABLE memory_processing_runs ADD COLUMN IF NOT EXISTS latency_ms INTEGER;
ALTER TABLE memory_processing_runs ADD COLUMN IF NOT EXISTS token_count_input INTEGER NOT NULL DEFAULT 0;
ALTER TABLE memory_processing_runs ADD COLUMN IF NOT EXISTS token_count_output INTEGER NOT NULL DEFAULT 0;
ALTER TABLE memory_processing_runs ADD COLUMN IF NOT EXISTS raw_output_ijson TEXT;
ALTER TABLE memory_processing_runs ADD COLUMN IF NOT EXISTS validation_errors_ijson TEXT;
ALTER TABLE memory_processing_runs ADD COLUMN IF NOT EXISTS error_text TEXT;

CREATE INDEX IF NOT EXISTS idx_memory_processing_runs_full_idempotency
ON memory_processing_runs(message_uuid, pipeline_version, prompt_bundle_version, input_content_hash, (COALESCE(model_profile_uuid, '')));

CREATE TABLE IF NOT EXISTS memory_gate_decisions (
    gate_decision_uuid TEXT PRIMARY KEY,
    text_unit_uuid TEXT NOT NULL REFERENCES text_units(text_unit_uuid),
    processing_run_uuid TEXT NOT NULL REFERENCES memory_processing_runs(processing_run_uuid),
    decision TEXT NOT NULL,
    reason_codes_ijson TEXT NOT NULL,
    salience_score DOUBLE PRECISION NOT NULL,
    persistence_score DOUBLE PRECISION NOT NULL,
    actionability_score DOUBLE PRECISION NOT NULL,
    sensitivity_score DOUBLE PRECISION NOT NULL,
    novelty_score DOUBLE PRECISION NOT NULL,
    requires_high_confidence_pass BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    schema_version INTEGER NOT NULL DEFAULT 1,
    UNIQUE(text_unit_uuid, processing_run_uuid)
);

CREATE TABLE IF NOT EXISTS linguistic_analyses (
    analysis_uuid TEXT PRIMARY KEY,
    text_unit_uuid TEXT NOT NULL REFERENCES text_units(text_unit_uuid),
    processing_run_uuid TEXT NOT NULL REFERENCES memory_processing_runs(processing_run_uuid),
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
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    schema_version INTEGER NOT NULL DEFAULT 1,
    UNIQUE(text_unit_uuid, processing_run_uuid)
);

CREATE INDEX IF NOT EXISTS idx_memory_gate_decisions_run ON memory_gate_decisions(processing_run_uuid);
CREATE INDEX IF NOT EXISTS idx_linguistic_analyses_run ON linguistic_analyses(processing_run_uuid);
