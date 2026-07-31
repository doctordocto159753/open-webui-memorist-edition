-- An alias is lookup evidence, not concept identity. Preserve ambiguity.
ALTER TABLE concept_aliases
DROP CONSTRAINT IF EXISTS concept_aliases_normalized_alias_key;

CREATE INDEX IF NOT EXISTS idx_concept_alias_normalized
ON concept_aliases(normalized_alias);
