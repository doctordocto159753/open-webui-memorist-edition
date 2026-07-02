CREATE TABLE IF NOT EXISTS openwebui_session_aliases (
    alias_uuid TEXT PRIMARY KEY,
    session_uuid TEXT NOT NULL,
    alias_type TEXT NOT NULL,
    alias_value TEXT NOT NULL,
    user_id TEXT,
    conversation_id TEXT,
    first_message_hash TEXT,
    client_session_nonce TEXT,
    confidence REAL NOT NULL DEFAULT 1.0,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY(session_uuid) REFERENCES sessions(session_uuid)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_openwebui_alias_unique
ON openwebui_session_aliases(alias_type, alias_value, COALESCE(user_id, ''));

CREATE INDEX IF NOT EXISTS idx_openwebui_alias_session
ON openwebui_session_aliases(session_uuid);

CREATE INDEX IF NOT EXISTS idx_openwebui_alias_fingerprint
ON openwebui_session_aliases(alias_type, first_message_hash, user_id, created_at);

CREATE TABLE IF NOT EXISTS openwebui_capture_dedup (
    dedup_key TEXT PRIMARY KEY,
    session_uuid TEXT NOT NULL,
    message_uuid TEXT,
    event_uuid TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(session_uuid) REFERENCES sessions(session_uuid),
    FOREIGN KEY(message_uuid) REFERENCES messages(message_uuid)
);
