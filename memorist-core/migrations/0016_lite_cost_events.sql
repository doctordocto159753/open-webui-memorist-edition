CREATE TABLE IF NOT EXISTS cost_events (
    cost_event_uuid TEXT PRIMARY KEY,
    model_profile_uuid TEXT,
    role TEXT NOT NULL,
    amount_ijson TEXT NOT NULL,
    created_at TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY(model_profile_uuid) REFERENCES model_profiles(model_profile_uuid)
);

CREATE INDEX IF NOT EXISTS idx_cost_events_model_role
ON cost_events(model_profile_uuid, role, created_at);
