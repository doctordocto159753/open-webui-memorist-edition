ALTER TABLE import_message_processing_status ADD COLUMN processing_decision TEXT;
ALTER TABLE import_message_processing_status ADD COLUMN pipeline_version TEXT;
ALTER TABLE import_message_processing_status ADD COLUMN prompt_bundle_version TEXT;
ALTER TABLE import_message_processing_status ADD COLUMN model_role TEXT;
ALTER TABLE import_message_processing_status ADD COLUMN processing_identity TEXT;
ALTER TABLE import_message_processing_status ADD COLUMN lease_owner TEXT;
ALTER TABLE import_message_processing_status ADD COLUMN lease_expires_at TEXT;
ALTER TABLE import_message_processing_status ADD COLUMN attempt_started_at TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_idempotency_key
ON jobs(idempotency_key)
WHERE idempotency_key IS NOT NULL;
