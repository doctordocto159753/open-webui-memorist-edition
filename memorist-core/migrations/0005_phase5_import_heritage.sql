CREATE TABLE IF NOT EXISTS import_runs (
    import_run_uuid TEXT PRIMARY KEY,
    source_platform TEXT,
    detected_format TEXT,
    detected_format_version TEXT,
    archive_sha256 TEXT NOT NULL,
    target_workspace_uuid TEXT,
    target_project_uuid TEXT,
    mode TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'uploaded',
    options_ijson TEXT NOT NULL,
    schema_fingerprint TEXT,
    total_files INTEGER DEFAULT 0,
    total_conversations INTEGER DEFAULT 0,
    total_messages INTEGER DEFAULT 0,
    imported_conversations INTEGER DEFAULT 0,
    imported_messages INTEGER DEFAULT 0,
    skipped_records INTEGER DEFAULT 0,
    warning_count INTEGER DEFAULT 0,
    error_count INTEGER DEFAULT 0,
    report_ijson TEXT,
    created_at TEXT NOT NULL,
    completed_at TEXT,
    schema_version INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_import_runs_archive_hash
ON import_runs(archive_sha256);

CREATE TABLE IF NOT EXISTS import_artifacts (
    import_artifact_uuid TEXT PRIMARY KEY,
    import_run_uuid TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    artifact_role TEXT NOT NULL,
    detected_media_type TEXT,
    size_bytes INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    object_store_path TEXT,
    parse_status TEXT NOT NULL DEFAULT 'pending',
    security_status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY(import_run_uuid) REFERENCES import_runs(import_run_uuid)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_import_artifact_path
ON import_artifacts(import_run_uuid, relative_path);

CREATE TABLE IF NOT EXISTS import_records (
    import_record_uuid TEXT PRIMARY KEY,
    import_run_uuid TEXT NOT NULL,
    import_artifact_uuid TEXT,
    source_record_type TEXT NOT NULL,
    source_record_id TEXT,
    source_parent_id TEXT,
    record_index INTEGER NOT NULL,
    raw_payload_ijson TEXT,
    normalized_payload_ijson TEXT,
    content_fingerprint TEXT NOT NULL,
    reconstruction_confidence REAL,
    status TEXT NOT NULL DEFAULT 'staged',
    created_at TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY(import_run_uuid) REFERENCES import_runs(import_run_uuid),
    FOREIGN KEY(import_artifact_uuid) REFERENCES import_artifacts(import_artifact_uuid)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_import_records_fingerprint
ON import_records(import_run_uuid, content_fingerprint);

CREATE TABLE IF NOT EXISTS import_mappings (
    import_mapping_uuid TEXT PRIMARY KEY,
    source_platform TEXT NOT NULL,
    source_object_type TEXT NOT NULL,
    source_object_id TEXT,
    source_fingerprint TEXT NOT NULL,
    target_object_type TEXT NOT NULL,
    target_uuid TEXT NOT NULL,
    import_run_uuid TEXT NOT NULL,
    created_at TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY(import_run_uuid) REFERENCES import_runs(import_run_uuid)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_import_mapping_source_dedupe
ON import_mappings(source_platform, source_object_type, COALESCE(source_object_id, source_fingerprint));

CREATE TABLE IF NOT EXISTS import_issues (
    import_issue_uuid TEXT PRIMARY KEY,
    import_run_uuid TEXT NOT NULL,
    severity TEXT NOT NULL,
    issue_code TEXT NOT NULL,
    artifact_path TEXT,
    source_record_id TEXT,
    message TEXT NOT NULL,
    details_ijson TEXT,
    created_at TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY(import_run_uuid) REFERENCES import_runs(import_run_uuid)
);

CREATE TABLE IF NOT EXISTS imported_conversations (
    imported_conversation_uuid TEXT PRIMARY KEY,
    import_run_uuid TEXT NOT NULL,
    source_platform TEXT NOT NULL,
    source_conversation_id TEXT,
    conversation_fingerprint TEXT NOT NULL,
    normalized_conversation_ijson TEXT NOT NULL,
    dry_run_decision TEXT NOT NULL DEFAULT 'new',
    commit_status TEXT NOT NULL DEFAULT 'pending',
    target_session_uuid TEXT,
    created_at TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY(import_run_uuid) REFERENCES import_runs(import_run_uuid)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_imported_conversation_fingerprint
ON imported_conversations(import_run_uuid, conversation_fingerprint);

CREATE TABLE IF NOT EXISTS import_dry_run_reports (
    import_run_uuid TEXT PRIMARY KEY,
    report_ijson TEXT NOT NULL,
    plan_fingerprint TEXT NOT NULL,
    created_at TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY(import_run_uuid) REFERENCES import_runs(import_run_uuid)
);

CREATE TABLE IF NOT EXISTS heritage_packages (
    heritage_package_uuid TEXT PRIMARY KEY,
    package_path TEXT NOT NULL,
    package_sha256 TEXT NOT NULL,
    manifest_ijson TEXT NOT NULL,
    export_mode TEXT NOT NULL,
    privacy_profile TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'created',
    created_at TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1
);
