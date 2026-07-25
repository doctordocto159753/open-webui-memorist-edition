CREATE TABLE IF NOT EXISTS openwebui_integration_outcomes (
    outcome_uuid TEXT PRIMARY KEY,
    user_uuid TEXT NOT NULL,
    workspace_uuid TEXT NOT NULL,
    stage TEXT NOT NULL,
    outcome TEXT NOT NULL,
    degraded_reason TEXT,
    detail_sanitized TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    schema_version INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_openwebui_outcomes_actor_created
ON openwebui_integration_outcomes(user_uuid, workspace_uuid, created_at DESC);
