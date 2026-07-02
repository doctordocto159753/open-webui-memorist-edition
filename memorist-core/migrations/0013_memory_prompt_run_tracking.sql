ALTER TABLE memory_processing_runs ADD COLUMN prompt_id TEXT;
ALTER TABLE memory_processing_runs ADD COLUMN prompt_version TEXT;
ALTER TABLE memory_processing_runs ADD COLUMN provider_type TEXT;
ALTER TABLE memory_processing_runs ADD COLUMN input_hash TEXT;
ALTER TABLE memory_processing_runs ADD COLUMN output_hash TEXT;

CREATE INDEX IF NOT EXISTS idx_memory_processing_runs_prompt
ON memory_processing_runs(prompt_id, prompt_version, created_at);
