CREATE TABLE IF NOT EXISTS memorist_actor_assertion_nonces (
    nonce TEXT PRIMARY KEY,
    user_uuid TEXT NOT NULL,
    workspace_uuid TEXT NOT NULL,
    purpose TEXT NOT NULL,
    expires_at INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

ALTER TABLE memory_context_attachments ADD COLUMN user_disposition TEXT NOT NULL DEFAULT 'none';
ALTER TABLE memory_context_attachments ADD COLUMN user_disposition_at TEXT;
ALTER TABLE memorist_regenerations ADD COLUMN source_response_link_uuid TEXT;
ALTER TABLE memorist_regenerations ADD COLUMN source_assistant_message_uuid TEXT;
ALTER TABLE assistant_response_links ADD COLUMN regeneration_uuid TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS idx_assistant_response_regeneration_once
ON assistant_response_links(regeneration_uuid) WHERE regeneration_uuid IS NOT NULL;
