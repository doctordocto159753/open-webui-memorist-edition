CREATE TABLE IF NOT EXISTS openwebui_message_captures (
    capture_uuid TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    openwebui_conversation_id TEXT,
    openwebui_message_id TEXT,
    role TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    session_uuid TEXT NOT NULL REFERENCES sessions(session_uuid),
    message_uuid TEXT NOT NULL REFERENCES messages(message_uuid),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    schema_version INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS openwebui_capture_dedup (
    dedup_key TEXT PRIMARY KEY,
    session_uuid TEXT NOT NULL REFERENCES sessions(session_uuid),
    message_uuid TEXT NOT NULL REFERENCES messages(message_uuid),
    event_uuid TEXT REFERENCES session_events(event_uuid),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    schema_version INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_openwebui_captures_session
ON openwebui_message_captures(session_uuid, created_at);

CREATE INDEX IF NOT EXISTS idx_openwebui_captures_message
ON openwebui_message_captures(message_uuid);

CREATE INDEX IF NOT EXISTS idx_openwebui_captures_conversation
ON openwebui_message_captures(openwebui_conversation_id, openwebui_message_id);
