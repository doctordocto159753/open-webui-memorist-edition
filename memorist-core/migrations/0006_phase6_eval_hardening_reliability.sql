CREATE TABLE IF NOT EXISTS security_attachment_reports (
    security_report_uuid TEXT PRIMARY KEY,
    attachment_uuid TEXT,
    instruction_like_content_detected INTEGER NOT NULL,
    untrusted_memory_count INTEGER NOT NULL,
    trusted_directive_count INTEGER NOT NULL,
    escaped_delimiter_count INTEGER NOT NULL,
    sensitive_item_excluded_count INTEGER NOT NULL,
    security_warnings_ijson TEXT NOT NULL,
    created_at TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS performance_reports (
    performance_report_uuid TEXT PRIMARY KEY,
    profile TEXT NOT NULL,
    report_ijson TEXT NOT NULL,
    created_at TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS reliability_reports (
    reliability_report_uuid TEXT PRIMARY KEY,
    report_type TEXT NOT NULL,
    status TEXT NOT NULL,
    report_ijson TEXT NOT NULL,
    created_at TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1
);
