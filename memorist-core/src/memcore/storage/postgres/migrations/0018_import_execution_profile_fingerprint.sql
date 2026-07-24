-- Preserve the exact configured execution profile selected when an import
-- item is scheduled, so later profile edits are rejected before provider use.
ALTER TABLE import_message_processing_status
ADD COLUMN IF NOT EXISTS execution_profile_fingerprint TEXT;
