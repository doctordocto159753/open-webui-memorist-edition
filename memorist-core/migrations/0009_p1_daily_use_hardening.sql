CREATE TABLE IF NOT EXISTS write_idempotency_records (
    idempotency_key TEXT PRIMARY KEY,
    command_type TEXT NOT NULL,
    target_type TEXT,
    target_uuid TEXT,
    result_ijson TEXT NOT NULL,
    created_at TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS import_progress (
    import_run_uuid TEXT PRIMARY KEY,
    phase TEXT NOT NULL DEFAULT 'idle',
    records_total INTEGER NOT NULL DEFAULT 0,
    records_done INTEGER NOT NULL DEFAULT 0,
    records_failed INTEGER NOT NULL DEFAULT 0,
    current_batch INTEGER NOT NULL DEFAULT 0,
    throttled INTEGER NOT NULL DEFAULT 0,
    throttle_reason TEXT,
    paused INTEGER NOT NULL DEFAULT 0,
    cancelled INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY(import_run_uuid) REFERENCES import_runs(import_run_uuid)
);

CREATE TABLE IF NOT EXISTS model_registry_entries (
    model_profile_uuid TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    model_name TEXT NOT NULL,
    model_aliases_ijson TEXT NOT NULL,
    context_window INTEGER,
    max_output_tokens_default INTEGER,
    tokenizer_family TEXT NOT NULL DEFAULT 'heuristic',
    supports_token_counting INTEGER NOT NULL DEFAULT 0,
    last_verified_at TEXT,
    source TEXT NOT NULL DEFAULT 'unknown',
    confidence REAL NOT NULL DEFAULT 0.5,
    created_at TEXT NOT NULL,
    updated_at TEXT,
    schema_version INTEGER NOT NULL DEFAULT 1
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_model_registry_provider_name
ON model_registry_entries(provider, model_name);

CREATE INDEX IF NOT EXISTS idx_model_registry_source
ON model_registry_entries(source);
