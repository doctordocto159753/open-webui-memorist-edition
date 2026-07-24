-- Processing workers must prove current ownership immediately before every
-- post-provider canonical write. The token prevents an owner name from being
-- reused across claims; generation gives each reclaim a monotonic fence.
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS lease_token TEXT;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS lease_generation BIGINT NOT NULL DEFAULT 0;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS lease_expires_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_jobs_processing_lease
ON jobs(job_uuid, status, locked_by, lease_generation, lease_token, lease_expires_at);
