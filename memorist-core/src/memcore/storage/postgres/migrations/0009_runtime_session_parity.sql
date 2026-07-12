ALTER TABLE sessions ADD COLUMN IF NOT EXISTS source_app TEXT NOT NULL DEFAULT 'open_webui';
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS privacy_scope TEXT NOT NULL DEFAULT 'local';
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS active_model_profile_uuid TEXT;
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS content_hash TEXT;

ALTER TABLE session_events ADD COLUMN IF NOT EXISTS payload_ijson TEXT;
ALTER TABLE session_events ALTER COLUMN payload_jsonb DROP NOT NULL;

ALTER TABLE messages ADD COLUMN IF NOT EXISTS parent_message_uuid TEXT;
ALTER TABLE messages ADD COLUMN IF NOT EXISTS conceptual_linguistic_model_uuid TEXT;
ALTER TABLE messages ADD COLUMN IF NOT EXISTS raw_payload_ijson TEXT;
ALTER TABLE messages ADD COLUMN IF NOT EXISTS snapshot_ijson TEXT;
ALTER TABLE messages ADD COLUMN IF NOT EXISTS content_hash TEXT;
ALTER TABLE messages ADD COLUMN IF NOT EXISTS token_count_input INTEGER DEFAULT 0;
ALTER TABLE messages ADD COLUMN IF NOT EXISTS token_count_output INTEGER DEFAULT 0;
ALTER TABLE messages ADD COLUMN IF NOT EXISTS redaction_status TEXT NOT NULL DEFAULT 'none';

ALTER TABLE message_versions ADD COLUMN IF NOT EXISTS raw_payload_ijson TEXT;
ALTER TABLE message_versions ADD COLUMN IF NOT EXISTS snapshot_ijson TEXT;
ALTER TABLE message_versions ADD COLUMN IF NOT EXISTS content_hash TEXT;

ALTER TABLE jobs ADD COLUMN IF NOT EXISTS payload_ijson TEXT;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS last_error TEXT;
ALTER TABLE jobs ALTER COLUMN payload_jsonb DROP NOT NULL;
ALTER TABLE jobs ALTER COLUMN run_after DROP NOT NULL;

ALTER TABLE memory_versions ADD COLUMN IF NOT EXISTS source_candidate_uuid TEXT;
