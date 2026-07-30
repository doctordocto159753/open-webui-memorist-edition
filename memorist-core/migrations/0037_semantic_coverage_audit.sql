-- PRD-02 WP02: content-free semantic coverage and candidate replay audit.

CREATE TABLE semantic_coverage_runs (
    coverage_run_uuid TEXT PRIMARY KEY,
    coverage_plan_version TEXT NOT NULL,
    coverage_hash TEXT NOT NULL UNIQUE,
    message_uuid TEXT NOT NULL,
    message_version_uuid TEXT,
    processing_run_uuid TEXT NOT NULL,
    semantic_prompt_execution_uuid TEXT,
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
    plan_ijson TEXT NOT NULL CHECK(json_valid(plan_ijson)),
    warnings_ijson TEXT NOT NULL CHECK(json_valid(warnings_ijson)),
    created_at TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY(message_uuid) REFERENCES messages(message_uuid),
    FOREIGN KEY(message_version_uuid) REFERENCES message_versions(message_version_uuid),
    FOREIGN KEY(processing_run_uuid) REFERENCES memory_processing_runs(processing_run_uuid),
    FOREIGN KEY(semantic_prompt_execution_uuid)
        REFERENCES prompt_execution_runs(prompt_execution_uuid)
);

CREATE INDEX idx_semantic_coverage_runs_message
ON semantic_coverage_runs(message_uuid, created_at);

CREATE TABLE semantic_coverage_items (
    coverage_item_uuid TEXT PRIMARY KEY,
    coverage_run_uuid TEXT NOT NULL,
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
    gate_decision_uuid TEXT,
    route_uuid TEXT,
    annotation_uuid TEXT,
    proposal_uuid TEXT UNIQUE,
    reason_codes_ijson TEXT NOT NULL CHECK(json_valid(reason_codes_ijson)),
    created_at TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1,
    CHECK(raw_start >= 0 AND raw_end > raw_start),
    CHECK(
        (disposition = 'durable_candidate' AND proposal_uuid IS NOT NULL)
        OR (disposition <> 'durable_candidate' AND proposal_uuid IS NULL)
    ),
    UNIQUE(coverage_run_uuid, semantic_unit_id),
    FOREIGN KEY(coverage_run_uuid)
        REFERENCES semantic_coverage_runs(coverage_run_uuid) ON DELETE CASCADE,
    FOREIGN KEY(gate_decision_uuid) REFERENCES memory_gate_decisions(gate_decision_uuid),
    FOREIGN KEY(route_uuid) REFERENCES memory_signal_routes(route_uuid),
    FOREIGN KEY(annotation_uuid) REFERENCES jakobson_sentence_annotations(annotation_uuid)
);

CREATE INDEX idx_semantic_coverage_items_run_span
ON semantic_coverage_items(coverage_run_uuid, raw_start, raw_end);

CREATE TABLE semantic_candidate_links (
    proposal_uuid TEXT PRIMARY KEY,
    coverage_item_uuid TEXT NOT NULL UNIQUE,
    candidate_uuid TEXT UNIQUE,
    payload_hash TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN (
        'candidate_creation_attempted',
        'candidate_linked'
    )),
    attempted_at TEXT NOT NULL,
    linked_at TEXT,
    updated_at TEXT NOT NULL,
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
    ),
    FOREIGN KEY(coverage_item_uuid)
        REFERENCES semantic_coverage_items(coverage_item_uuid) ON DELETE CASCADE,
    FOREIGN KEY(candidate_uuid) REFERENCES memory_candidates(candidate_uuid)
);
