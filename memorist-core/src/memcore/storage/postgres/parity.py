from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from memcore.storage.migrations import default_migrations_dir, migration_files
from memcore.storage.postgres.migrations import postgres_migration_files

REQUIRED_CANONICAL_TABLES = {
    "workspaces",
    "projects",
    "sessions",
    "openwebui_session_aliases",
    "session_events",
    "messages",
    "message_versions",
    "text_units",
    "jakobson_analysis_runs",
    "jakobson_sentence_annotations",
    "memory_signal_routes",
    "model_profiles",
    "model_role_defaults",
    "model_usage_events",
    "model_health_events",
    "model_privacy_acknowledgements",
    "embedding_records",
    "jobs",
    "job_attempts",
    "import_runs",
    "import_records",
    "import_mappings",
    "import_message_processing_status",
    "memory_processing_runs",
    "memory_candidates",
    "candidate_evidence",
    "memories",
    "memory_versions",
    "memory_evidence_links",
    "memory_blocks",
    "memory_block_versions",
    "memory_block_sources",
    "memory_context_attachments",
    "prompt_execution_runs",
    "import_reconstruction_runs",
    "privacy_sensitivity_reviews",
    "retrieval_runs",
    "retrieval_candidates",
    "privacy_requests",
    "privacy_request_items",
    "erasure_receipts",
    "graph_projection_outbox",
    "embedding_outbox",
    "cost_events",
}

DOCUMENTED_FULL_ONLY_TABLES = {
    "embedding_outbox",
    "job_attempts",
    "memory_processing_outbox",
    "privacy_erasure_outbox",
}

TABLE_RE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?P<name>[a-zA-Z_][a-zA-Z0-9_]*)",
    re.IGNORECASE,
)


def sqlite_tables(migrations_dir: Path | None = None) -> set[str]:
    resolved = default_migrations_dir() if migrations_dir is None else migrations_dir
    return _tables_from_files(migration_files(resolved))


def postgres_tables(migrations_dir: Path | None = None) -> set[str]:
    return _tables_from_files(postgres_migration_files(migrations_dir))


def build_parity_report() -> dict[str, Any]:
    sqlite = sqlite_tables()
    postgres = postgres_tables()
    missing_in_postgres = sorted(REQUIRED_CANONICAL_TABLES - postgres)
    missing_in_sqlite = sorted((REQUIRED_CANONICAL_TABLES - DOCUMENTED_FULL_ONLY_TABLES) - sqlite)
    return {
        "status": "pass" if not missing_in_postgres else "fail",
        "required_tables": sorted(REQUIRED_CANONICAL_TABLES),
        "sqlite_tables": sorted(sqlite),
        "postgres_tables": sorted(postgres),
        "missing_in_postgres": missing_in_postgres,
        "missing_in_sqlite": missing_in_sqlite,
        "documented_full_only_tables": sorted(DOCUMENTED_FULL_ONLY_TABLES),
    }


def parity_report_json() -> str:
    return json.dumps(build_parity_report(), indent=2, sort_keys=True)


def _tables_from_files(files: list[Path]) -> set[str]:
    tables: set[str] = set()
    for path in files:
        tables.update(match.group("name") for match in TABLE_RE.finditer(path.read_text()))
    return tables
