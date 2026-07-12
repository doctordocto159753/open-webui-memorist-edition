ALTER TABLE import_message_processing_status ADD COLUMN IF NOT EXISTS processing_attempt_uuid TEXT;

CREATE INDEX IF NOT EXISTS idx_import_processing_attempt_fence
ON import_message_processing_status(status_uuid, lease_owner, processing_attempt_uuid, status);
