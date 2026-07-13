CREATE TABLE IF NOT EXISTS memorist_actor_assertion_nonces (
    nonce TEXT PRIMARY KEY,
    user_uuid TEXT NOT NULL,
    workspace_uuid TEXT NOT NULL,
    purpose TEXT NOT NULL,
    expires_at BIGINT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE memory_context_attachments
ADD COLUMN IF NOT EXISTS user_disposition TEXT NOT NULL DEFAULT 'none';
ALTER TABLE memory_context_attachments
ADD COLUMN IF NOT EXISTS user_disposition_at TIMESTAMPTZ;
ALTER TABLE memorist_regenerations
ADD COLUMN IF NOT EXISTS source_response_link_uuid TEXT;
ALTER TABLE memorist_regenerations
ADD COLUMN IF NOT EXISTS source_assistant_message_uuid TEXT;
ALTER TABLE assistant_response_links
ADD COLUMN IF NOT EXISTS regeneration_uuid TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS idx_assistant_response_regeneration_once
ON assistant_response_links(regeneration_uuid) WHERE regeneration_uuid IS NOT NULL;
