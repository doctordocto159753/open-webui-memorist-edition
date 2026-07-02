CREATE TABLE IF NOT EXISTS heavy_operation_reports (
    report_uuid TEXT PRIMARY KEY,
    report_type TEXT NOT NULL,
    subject_uuid TEXT,
    report_ijson TEXT NOT NULL,
    created_at TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_heavy_operation_reports_type
ON heavy_operation_reports(report_type, created_at);

CREATE TABLE IF NOT EXISTS recovery_actions (
    recovery_action_uuid TEXT PRIMARY KEY,
    target_type TEXT NOT NULL,
    target_uuid TEXT,
    action_ijson TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1
);

