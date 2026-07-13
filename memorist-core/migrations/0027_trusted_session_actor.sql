CREATE TABLE IF NOT EXISTS memorist_session_actors (
    session_uuid TEXT PRIMARY KEY,
    user_uuid TEXT NOT NULL,
    workspace_uuid TEXT NOT NULL,
    created_at TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY(session_uuid) REFERENCES sessions(session_uuid)
);

CREATE INDEX IF NOT EXISTS idx_memorist_session_actor_scope
ON memorist_session_actors(user_uuid, workspace_uuid, session_uuid);
