-- An alias is lookup evidence, not concept identity. The same short form can
-- refer to multiple disambiguated canonical concepts.

CREATE TABLE concept_aliases_v2 (
    concept_uuid TEXT NOT NULL,
    alias TEXT NOT NULL,
    normalized_alias TEXT NOT NULL,
    language TEXT,
    PRIMARY KEY(concept_uuid, normalized_alias),
    FOREIGN KEY(concept_uuid) REFERENCES canonical_concepts(concept_uuid) ON DELETE CASCADE
);

INSERT INTO concept_aliases_v2 (concept_uuid, alias, normalized_alias, language)
SELECT concept_uuid, alias, normalized_alias, language FROM concept_aliases;

DROP TABLE concept_aliases;
ALTER TABLE concept_aliases_v2 RENAME TO concept_aliases;
CREATE INDEX idx_concept_alias_normalized ON concept_aliases(normalized_alias);
