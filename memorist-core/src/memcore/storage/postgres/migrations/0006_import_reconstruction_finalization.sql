ALTER TABLE import_message_processing_status ADD COLUMN IF NOT EXISTS processing_decision TEXT;
ALTER TABLE import_message_processing_status ADD COLUMN IF NOT EXISTS pipeline_version TEXT;
ALTER TABLE import_message_processing_status ADD COLUMN IF NOT EXISTS prompt_bundle_version TEXT;
ALTER TABLE import_message_processing_status ADD COLUMN IF NOT EXISTS model_role TEXT;
ALTER TABLE import_message_processing_status ADD COLUMN IF NOT EXISTS processing_identity TEXT;
ALTER TABLE import_message_processing_status ADD COLUMN IF NOT EXISTS lease_owner TEXT;
ALTER TABLE import_message_processing_status ADD COLUMN IF NOT EXISTS lease_expires_at TIMESTAMPTZ;
ALTER TABLE import_message_processing_status ADD COLUMN IF NOT EXISTS attempt_started_at TIMESTAMPTZ;

CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_idempotency_key
ON jobs(idempotency_key)
WHERE idempotency_key IS NOT NULL;
