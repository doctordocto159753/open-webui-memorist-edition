-- Provider contract execution audit (PostgreSQL). Mirror of SQLite 0034: record
-- the truthful provider attempt / repair / fallback outcome so a schema-invalid
-- provider response is auditable instead of disappearing before persistence.
ALTER TABLE processing_stage_runs ADD COLUMN IF NOT EXISTS provider_output_valid BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE processing_stage_runs ADD COLUMN IF NOT EXISTS repair_attempted BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE processing_stage_runs ADD COLUMN IF NOT EXISTS repair_succeeded BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE processing_stage_runs ADD COLUMN IF NOT EXISTS parse_status TEXT;
ALTER TABLE processing_stage_runs ADD COLUMN IF NOT EXISTS capability_mode TEXT;
ALTER TABLE processing_stage_runs ADD COLUMN IF NOT EXISTS provider_response_id TEXT;
