ALTER TABLE import_runs ALTER COLUMN source_platform DROP NOT NULL;
ALTER TABLE import_runs ALTER COLUMN archive_hash DROP NOT NULL;
ALTER TABLE import_runs ALTER COLUMN archive_hash SET DEFAULT '';
ALTER TABLE import_runs ADD COLUMN IF NOT EXISTS archive_sha256 TEXT;
ALTER TABLE import_runs ADD COLUMN IF NOT EXISTS mode TEXT;
ALTER TABLE import_runs ADD COLUMN IF NOT EXISTS options_ijson TEXT;
ALTER TABLE import_runs ADD COLUMN IF NOT EXISTS target_workspace_uuid TEXT REFERENCES workspaces(workspace_uuid);
ALTER TABLE import_runs ADD COLUMN IF NOT EXISTS target_project_uuid TEXT REFERENCES projects(project_uuid);
ALTER TABLE import_runs ADD COLUMN IF NOT EXISTS detected_format TEXT;
ALTER TABLE import_runs ADD COLUMN IF NOT EXISTS detected_format_version TEXT;
ALTER TABLE import_runs ADD COLUMN IF NOT EXISTS total_files INTEGER NOT NULL DEFAULT 0;
ALTER TABLE import_runs ADD COLUMN IF NOT EXISTS total_conversations INTEGER NOT NULL DEFAULT 0;
ALTER TABLE import_runs ADD COLUMN IF NOT EXISTS total_messages INTEGER NOT NULL DEFAULT 0;
ALTER TABLE import_runs ADD COLUMN IF NOT EXISTS imported_conversations INTEGER NOT NULL DEFAULT 0;
ALTER TABLE import_runs ADD COLUMN IF NOT EXISTS imported_messages INTEGER NOT NULL DEFAULT 0;
ALTER TABLE import_runs ADD COLUMN IF NOT EXISTS skipped_records INTEGER NOT NULL DEFAULT 0;
ALTER TABLE import_runs ADD COLUMN IF NOT EXISTS warning_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE import_runs ADD COLUMN IF NOT EXISTS error_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE import_runs ADD COLUMN IF NOT EXISTS report_ijson TEXT;
ALTER TABLE import_runs ADD COLUMN IF NOT EXISTS schema_fingerprint TEXT;
ALTER TABLE import_runs ADD COLUMN IF NOT EXISTS completed_at TIMESTAMPTZ;

CREATE TABLE IF NOT EXISTS import_artifacts (
    import_artifact_uuid TEXT PRIMARY KEY,
    import_run_uuid TEXT NOT NULL REFERENCES import_runs(import_run_uuid),
    relative_path TEXT NOT NULL,
    artifact_role TEXT NOT NULL,
    detected_media_type TEXT,
    size_bytes INTEGER NOT NULL DEFAULT 0,
    sha256 TEXT NOT NULL,
    object_store_path TEXT NOT NULL,
    parse_status TEXT NOT NULL DEFAULT 'pending',
    security_status TEXT NOT NULL DEFAULT 'passed',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    schema_version INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS import_issues (
    import_issue_uuid TEXT PRIMARY KEY,
    import_run_uuid TEXT NOT NULL REFERENCES import_runs(import_run_uuid),
    severity TEXT NOT NULL,
    issue_code TEXT NOT NULL,
    artifact_path TEXT,
    source_record_id TEXT,
    message TEXT NOT NULL,
    details_ijson TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    schema_version INTEGER NOT NULL DEFAULT 1
);

ALTER TABLE import_records ADD COLUMN IF NOT EXISTS import_artifact_uuid TEXT REFERENCES import_artifacts(import_artifact_uuid);
ALTER TABLE import_records ADD COLUMN IF NOT EXISTS source_record_type TEXT;
ALTER TABLE import_records ADD COLUMN IF NOT EXISTS source_parent_id TEXT;
ALTER TABLE import_records ADD COLUMN IF NOT EXISTS record_index INTEGER;
ALTER TABLE import_records ADD COLUMN IF NOT EXISTS raw_payload_ijson TEXT;
ALTER TABLE import_records ADD COLUMN IF NOT EXISTS normalized_payload_ijson TEXT;
ALTER TABLE import_records ADD COLUMN IF NOT EXISTS content_fingerprint TEXT;
ALTER TABLE import_records ADD COLUMN IF NOT EXISTS reconstruction_confidence REAL;
ALTER TABLE import_records ALTER COLUMN source_record_id DROP NOT NULL;
ALTER TABLE import_records ALTER COLUMN record_type DROP NOT NULL;
ALTER TABLE import_records ALTER COLUMN status SET DEFAULT 'staged';
ALTER TABLE import_records ALTER COLUMN payload_jsonb DROP NOT NULL;
ALTER TABLE import_records ALTER COLUMN normalized_hash DROP NOT NULL;

CREATE TABLE IF NOT EXISTS imported_conversations (
    imported_conversation_uuid TEXT PRIMARY KEY,
    import_run_uuid TEXT NOT NULL REFERENCES import_runs(import_run_uuid),
    source_platform TEXT NOT NULL,
    source_conversation_id TEXT,
    conversation_fingerprint TEXT NOT NULL,
    normalized_conversation_ijson TEXT NOT NULL,
    dry_run_decision TEXT NOT NULL DEFAULT 'new',
    commit_status TEXT NOT NULL DEFAULT 'pending',
    target_session_uuid TEXT REFERENCES sessions(session_uuid),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    schema_version INTEGER NOT NULL DEFAULT 1,
    UNIQUE(import_run_uuid, conversation_fingerprint)
);

CREATE TABLE IF NOT EXISTS import_dry_run_reports (
    import_run_uuid TEXT PRIMARY KEY REFERENCES import_runs(import_run_uuid),
    report_ijson TEXT NOT NULL,
    plan_fingerprint TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    schema_version INTEGER NOT NULL DEFAULT 1
);

ALTER TABLE import_mappings ADD COLUMN IF NOT EXISTS import_mapping_uuid TEXT;
ALTER TABLE import_mappings ADD COLUMN IF NOT EXISTS source_platform TEXT;
ALTER TABLE import_mappings ADD COLUMN IF NOT EXISTS source_object_type TEXT;
ALTER TABLE import_mappings ADD COLUMN IF NOT EXISTS source_object_id TEXT;
ALTER TABLE import_mappings ADD COLUMN IF NOT EXISTS source_fingerprint TEXT;
ALTER TABLE import_mappings ADD COLUMN IF NOT EXISTS target_object_type TEXT;
ALTER TABLE import_mappings ALTER COLUMN import_record_uuid DROP NOT NULL;
ALTER TABLE import_mappings ALTER COLUMN source_type DROP NOT NULL;
ALTER TABLE import_mappings ALTER COLUMN source_uuid DROP NOT NULL;
ALTER TABLE import_mappings ALTER COLUMN target_type DROP NOT NULL;

CREATE TABLE IF NOT EXISTS import_progress (
    import_run_uuid TEXT PRIMARY KEY REFERENCES import_runs(import_run_uuid),
    phase TEXT NOT NULL,
    records_total INTEGER NOT NULL DEFAULT 0,
    records_done INTEGER NOT NULL DEFAULT 0,
    records_failed INTEGER NOT NULL DEFAULT 0,
    current_batch INTEGER NOT NULL DEFAULT 0,
    paused INTEGER NOT NULL DEFAULT 0,
    cancelled INTEGER NOT NULL DEFAULT 0,
    throttled INTEGER NOT NULL DEFAULT 0,
    throttle_reason TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    schema_version INTEGER NOT NULL DEFAULT 1
);

ALTER TABLE import_message_processing_status ADD COLUMN IF NOT EXISTS lease_owner TEXT;
ALTER TABLE import_message_processing_status ADD COLUMN IF NOT EXISTS lease_expires_at TIMESTAMPTZ;
ALTER TABLE import_message_processing_status ADD COLUMN IF NOT EXISTS attempt_started_at TIMESTAMPTZ;
ALTER TABLE import_message_processing_status ADD COLUMN IF NOT EXISTS run_after TIMESTAMPTZ;
