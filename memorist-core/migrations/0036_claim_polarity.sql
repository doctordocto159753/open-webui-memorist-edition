-- PRD-02 WP01: claim polarity as a first-class semantic field.
--
-- Polarity is separated from extraction confidence. A negated claim is
-- asserted as certainly as its positive equivalent, so the two must carry the
-- same confidence and differ only here.
--
-- Additive and non-destructive. Existing rows default to 'unknown' because
-- polarity was never recorded for them: no historical confidence is
-- recalculated and no historical evidence is rewritten. Backfilling them as
-- 'affirmed' would invent a decision the old pipeline never made.

ALTER TABLE memory_candidates ADD COLUMN polarity TEXT NOT NULL DEFAULT 'unknown';
ALTER TABLE memory_versions ADD COLUMN polarity TEXT NOT NULL DEFAULT 'unknown';

CREATE INDEX IF NOT EXISTS idx_memory_candidates_polarity
ON memory_candidates(polarity);
