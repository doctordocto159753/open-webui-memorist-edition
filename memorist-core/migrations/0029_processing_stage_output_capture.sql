-- Idempotent stage replay must return the originally validated output.
-- Without this column a replayed privacy/high-confidence stage loses its
-- decision and deterministic retry can downgrade candidate lifecycle state.
ALTER TABLE processing_stage_runs ADD COLUMN output_ijson TEXT;
