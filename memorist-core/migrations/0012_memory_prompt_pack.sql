CREATE TABLE IF NOT EXISTS memory_prompt_invocations (
    prompt_invocation_uuid TEXT PRIMARY KEY,
    prompt_id TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    model_profile_uuid TEXT,
    provider_type TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    output_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY(model_profile_uuid) REFERENCES model_profiles(model_profile_uuid)
);

CREATE INDEX IF NOT EXISTS idx_memory_prompt_invocations_prompt
ON memory_prompt_invocations(prompt_id, prompt_version, created_at);

CREATE INDEX IF NOT EXISTS idx_memory_prompt_invocations_model_profile
ON memory_prompt_invocations(model_profile_uuid, created_at);
