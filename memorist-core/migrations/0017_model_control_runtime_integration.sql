ALTER TABLE model_profiles ADD COLUMN profile_name TEXT;
ALTER TABLE model_profiles ADD COLUMN quality_profile_ijson TEXT;
ALTER TABLE model_profiles ADD COLUMN latency_profile_ijson TEXT;
ALTER TABLE model_profiles ADD COLUMN requires_privacy_acknowledgement INTEGER NOT NULL DEFAULT 0;

ALTER TABLE model_usage_events ADD COLUMN unit_uuid TEXT;
ALTER TABLE model_usage_events ADD COLUMN annotation_uuid TEXT;

ALTER TABLE embedding_records ADD COLUMN embedding_dimension INTEGER;

CREATE TABLE IF NOT EXISTS model_health_events (
    health_event_uuid TEXT PRIMARY KEY,
    model_profile_uuid TEXT,
    role TEXT NOT NULL,
    provider_type TEXT NOT NULL,
    model_name TEXT NOT NULL,
    status TEXT NOT NULL,
    latency_ms INTEGER,
    local_only_safe INTEGER NOT NULL DEFAULT 1,
    detail_sanitized TEXT,
    created_at TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY(model_profile_uuid) REFERENCES model_profiles(model_profile_uuid)
);

CREATE INDEX IF NOT EXISTS idx_model_health_events_profile_created
ON model_health_events(model_profile_uuid, created_at);

CREATE INDEX IF NOT EXISTS idx_model_health_events_role_created
ON model_health_events(role, created_at);

CREATE TABLE IF NOT EXISTS model_privacy_acknowledgements (
    ack_uuid TEXT PRIMARY KEY,
    model_profile_uuid TEXT NOT NULL,
    role TEXT NOT NULL,
    acknowledged_risk_level TEXT NOT NULL,
    acknowledged_data_sent_ijson TEXT NOT NULL,
    acknowledged_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY(model_profile_uuid) REFERENCES model_profiles(model_profile_uuid)
);

CREATE INDEX IF NOT EXISTS idx_model_privacy_acknowledgements_profile_created
ON model_privacy_acknowledgements(model_profile_uuid, created_at);

CREATE INDEX IF NOT EXISTS idx_model_usage_events_role_stage_created
ON model_usage_events(role, stage, created_at);

CREATE INDEX IF NOT EXISTS idx_model_usage_events_scope_created
ON model_usage_events(workspace_uuid, project_uuid, session_uuid, created_at);
