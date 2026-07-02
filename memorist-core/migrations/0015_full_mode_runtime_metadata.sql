ALTER TABLE jobs ADD COLUMN idempotency_key TEXT;
ALTER TABLE jobs ADD COLUMN locked_by TEXT;
ALTER TABLE jobs ADD COLUMN locked_at TEXT;
ALTER TABLE jobs ADD COLUMN last_error_sanitized TEXT;

UPDATE jobs
SET last_error_sanitized = last_error
WHERE last_error_sanitized IS NULL AND last_error IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_jobs_status_priority_run_after
ON jobs(status, priority DESC, run_after, created_at);

CREATE INDEX IF NOT EXISTS idx_jobs_locked_at
ON jobs(status, locked_at)
WHERE status = 'running';

CREATE TABLE IF NOT EXISTS import_commit_batches (
    batch_uuid TEXT PRIMARY KEY,
    import_run_uuid TEXT NOT NULL,
    batch_number INTEGER NOT NULL,
    batch_fingerprint TEXT NOT NULL,
    status TEXT NOT NULL,
    record_count INTEGER NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    error_sanitized TEXT,
    created_at TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY(import_run_uuid) REFERENCES import_runs(import_run_uuid)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_import_commit_batches_number
ON import_commit_batches(import_run_uuid, batch_number);

CREATE UNIQUE INDEX IF NOT EXISTS idx_import_commit_batches_fingerprint
ON import_commit_batches(import_run_uuid, batch_fingerprint);

CREATE INDEX IF NOT EXISTS idx_import_commit_batches_status
ON import_commit_batches(import_run_uuid, status);
