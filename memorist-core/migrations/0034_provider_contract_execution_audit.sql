-- Provider contract execution audit. A schema-invalid provider response must be
-- recorded truthfully (attempt, repair, fallback) instead of vanishing when
-- validation fails. These columns are additive and default to the pre-fix
-- behaviour so historical rows stay valid.
ALTER TABLE processing_stage_runs ADD COLUMN provider_output_valid INTEGER NOT NULL DEFAULT 0;
ALTER TABLE processing_stage_runs ADD COLUMN repair_attempted INTEGER NOT NULL DEFAULT 0;
ALTER TABLE processing_stage_runs ADD COLUMN repair_succeeded INTEGER NOT NULL DEFAULT 0;
ALTER TABLE processing_stage_runs ADD COLUMN parse_status TEXT;
ALTER TABLE processing_stage_runs ADD COLUMN capability_mode TEXT;
ALTER TABLE processing_stage_runs ADD COLUMN provider_response_id TEXT;
