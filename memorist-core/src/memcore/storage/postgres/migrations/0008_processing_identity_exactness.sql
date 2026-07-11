ALTER TABLE memory_processing_runs ADD COLUMN IF NOT EXISTS model_role TEXT;
ALTER TABLE memory_processing_runs ADD COLUMN IF NOT EXISTS model_name TEXT;
ALTER TABLE memory_processing_runs ADD COLUMN IF NOT EXISTS processing_identity_hash TEXT;
ALTER TABLE import_message_processing_status ADD COLUMN IF NOT EXISTS model_role TEXT;
ALTER TABLE import_message_processing_status ADD COLUMN IF NOT EXISTS prompt_bundle_version TEXT;
ALTER TABLE import_message_processing_status ADD COLUMN IF NOT EXISTS prompt_id TEXT;
ALTER TABLE import_message_processing_status ADD COLUMN IF NOT EXISTS prompt_version TEXT;
ALTER TABLE import_message_processing_status ADD COLUMN IF NOT EXISTS pipeline_version TEXT;
CREATE INDEX IF NOT EXISTS idx_memory_processing_exact_identity ON memory_processing_runs(processing_identity_hash, status);
DROP INDEX IF EXISTS idx_processing_run_idempotency;
CREATE UNIQUE INDEX IF NOT EXISTS idx_processing_run_exact_idempotency
ON memory_processing_runs(message_uuid, pipeline_version, input_content_hash, COALESCE(prompt_bundle_version, ''), COALESCE(prompt_id, ''), COALESCE(prompt_version, ''), COALESCE(model_role, ''), COALESCE(model_profile_uuid, ''), COALESCE(provider_type, ''), COALESCE(model_name, ''), COALESCE(processing_identity_hash, ''));
CREATE INDEX IF NOT EXISTS idx_processing_run_idempotency
ON memory_processing_runs(message_uuid, pipeline_version, input_content_hash);
