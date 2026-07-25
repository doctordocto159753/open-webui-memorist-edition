ALTER TABLE model_health_events ADD COLUMN profile_fingerprint TEXT;
CREATE INDEX IF NOT EXISTS idx_model_health_events_certification
ON model_health_events(model_profile_uuid, profile_fingerprint, created_at);
