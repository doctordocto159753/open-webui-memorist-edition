CREATE TABLE IF NOT EXISTS openwebui_message_captures (
    capture_uuid TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    openwebui_conversation_id TEXT,
    openwebui_message_id TEXT,
    role TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    session_uuid TEXT NOT NULL,
    message_uuid TEXT NOT NULL,
    created_at TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY (session_uuid) REFERENCES sessions(session_uuid),
    FOREIGN KEY (message_uuid) REFERENCES messages(message_uuid)
);

CREATE INDEX IF NOT EXISTS idx_openwebui_captures_session
ON openwebui_message_captures(session_uuid, created_at);

CREATE TABLE IF NOT EXISTS openwebui_integration_status (
    status_uuid TEXT PRIMARY KEY,
    status_key TEXT NOT NULL UNIQUE,
    value_ijson TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1
);
