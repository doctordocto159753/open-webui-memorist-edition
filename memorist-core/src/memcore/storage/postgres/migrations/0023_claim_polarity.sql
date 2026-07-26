-- PRD-02 WP01: claim polarity as a first-class semantic field (Full mode).
--
-- Mirrors migrations/0036_claim_polarity.sql so Lite and Full store the same
-- semantic decision in the same shape.
--
-- Additive, idempotent, and non-destructive. Existing rows default to
-- 'unknown' because polarity was never recorded for them.

ALTER TABLE memory_candidates ADD COLUMN IF NOT EXISTS polarity TEXT NOT NULL DEFAULT 'unknown';
ALTER TABLE memory_versions ADD COLUMN IF NOT EXISTS polarity TEXT NOT NULL DEFAULT 'unknown';

CREATE INDEX IF NOT EXISTS idx_memory_candidates_polarity
ON memory_candidates(polarity);
