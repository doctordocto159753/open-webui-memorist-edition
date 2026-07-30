-- PRD-02 WP02: content-free semantic coverage and candidate replay audit.

ALTER TABLE candidate_evidence
    ADD COLUMN IF NOT EXISTS evidence_role TEXT NOT NULL DEFAULT 'primary'
        CHECK(evidence_role IN ('primary', 'secondary'));

ALTER TABLE candidate_evidence
    ADD COLUMN IF NOT EXISTS support_type TEXT NOT NULL DEFAULT 'supporting'
        CHECK(support_type IN ('supporting', 'contradicting'));

CREATE TABLE IF NOT EXISTS semantic_coverage_runs (
    coverage_run_uuid TEXT PRIMARY KEY,
    coverage_plan_version TEXT NOT NULL,
    coverage_hash TEXT NOT NULL UNIQUE,
    message_uuid TEXT NOT NULL REFERENCES messages(message_uuid),
    message_version_uuid TEXT REFERENCES message_versions(message_version_uuid),
    processing_run_uuid TEXT NOT NULL REFERENCES memory_processing_runs(processing_run_uuid),
    semantic_prompt_execution_uuid TEXT
        REFERENCES prompt_execution_runs(prompt_execution_uuid),
    raw_text_hash TEXT NOT NULL,
    text_envelope_contract_version TEXT NOT NULL,
    semantic_contract_hash TEXT NOT NULL,
    route_mapping_version TEXT NOT NULL
        DEFAULT 'pr4d-route-candidate-mapper-v1',
    provenance_policy_version TEXT NOT NULL
        DEFAULT 'pr4d-provenance-policy-v1',
    privacy_policy_version TEXT NOT NULL
        DEFAULT 'wp02-privacy-ceiling-v1',
    status TEXT NOT NULL
        CHECK(status IN ('complete', 'abstain', 'retain_raw_only', 'needs_review')),
    plan_jsonb JSONB NOT NULL CHECK(jsonb_typeof(plan_jsonb) = 'object'),
    warnings_jsonb JSONB NOT NULL CHECK(jsonb_typeof(warnings_jsonb) = 'array'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    schema_version INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_semantic_coverage_runs_message
ON semantic_coverage_runs(message_uuid, created_at);

CREATE TABLE IF NOT EXISTS semantic_coverage_items (
    coverage_item_uuid TEXT PRIMARY KEY,
    coverage_run_uuid TEXT NOT NULL
        REFERENCES semantic_coverage_runs(coverage_run_uuid) ON DELETE CASCADE,
    semantic_unit_id TEXT,
    semantic_unit_fingerprint TEXT,
    raw_start INTEGER NOT NULL,
    raw_end INTEGER NOT NULL,
    disposition TEXT NOT NULL CHECK(disposition IN (
        'durable_candidate',
        'context_only',
        'transient_instruction',
        'unresolved_reference',
        'rejected_by_gate',
        'needs_review',
        'unsupported'
    )),
    gate_decision_uuid TEXT REFERENCES memory_gate_decisions(gate_decision_uuid),
    route_uuid TEXT REFERENCES memory_signal_routes(route_uuid),
    annotation_uuid TEXT REFERENCES jakobson_sentence_annotations(annotation_uuid),
    proposal_uuid TEXT UNIQUE,
    reason_codes_jsonb JSONB NOT NULL CHECK(jsonb_typeof(reason_codes_jsonb) = 'array'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    schema_version INTEGER NOT NULL DEFAULT 1,
    CHECK(raw_start >= 0 AND raw_end > raw_start),
    CHECK(
        (disposition = 'durable_candidate' AND proposal_uuid IS NOT NULL)
        OR (disposition <> 'durable_candidate' AND proposal_uuid IS NULL)
    ),
    UNIQUE(coverage_run_uuid, semantic_unit_id)
);

CREATE INDEX IF NOT EXISTS idx_semantic_coverage_items_run_span
ON semantic_coverage_items(coverage_run_uuid, raw_start, raw_end);

CREATE TABLE IF NOT EXISTS semantic_candidate_links (
    proposal_uuid TEXT PRIMARY KEY,
    coverage_item_uuid TEXT NOT NULL UNIQUE
        REFERENCES semantic_coverage_items(coverage_item_uuid) ON DELETE CASCADE,
    candidate_uuid TEXT UNIQUE REFERENCES memory_candidates(candidate_uuid),
    payload_hash TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN (
        'candidate_creation_attempted',
        'candidate_linked'
    )),
    attempted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    linked_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    schema_version INTEGER NOT NULL DEFAULT 1,
    CHECK(
        (state = 'candidate_creation_attempted'
            AND candidate_uuid IS NULL
            AND linked_at IS NULL)
        OR
        (state = 'candidate_linked'
            AND candidate_uuid IS NOT NULL
            AND candidate_uuid = proposal_uuid
            AND linked_at IS NOT NULL)
    )
);
