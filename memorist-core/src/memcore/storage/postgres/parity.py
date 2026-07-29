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
    "semantic_coverage_runs",
    "semantic_coverage_items",
    "semantic_candidate_links",
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

WP02_REQUIRED_COLUMNS = {
    "semantic_coverage_runs": {
        "coverage_run_uuid",
        "coverage_plan_version",
        "coverage_hash",
        "message_uuid",
        "message_version_uuid",
        "processing_run_uuid",
        "semantic_prompt_execution_uuid",
        "raw_text_hash",
        "text_envelope_contract_version",
        "semantic_contract_hash",
        "route_mapping_version",
        "provenance_policy_version",
        "privacy_policy_version",
        "status",
        "plan_json",
        "warnings_json",
        "created_at",
        "schema_version",
    },
    "semantic_coverage_items": {
        "coverage_item_uuid",
        "coverage_run_uuid",
        "semantic_unit_id",
        "semantic_unit_fingerprint",
        "raw_start",
        "raw_end",
        "disposition",
        "gate_decision_uuid",
        "route_uuid",
        "annotation_uuid",
        "proposal_uuid",
        "reason_codes_json",
        "created_at",
        "schema_version",
    },
    "semantic_candidate_links": {
        "proposal_uuid",
        "coverage_item_uuid",
        "candidate_uuid",
        "payload_hash",
        "state",
        "attempted_at",
        "linked_at",
        "updated_at",
        "schema_version",
    },
    "candidate_evidence": {"evidence_role", "support_type"},
}

_NULLABLE_WP02_COLUMNS = {
    ("semantic_coverage_runs", "message_version_uuid"),
    ("semantic_coverage_runs", "semantic_prompt_execution_uuid"),
    ("semantic_coverage_items", "semantic_unit_id"),
    ("semantic_coverage_items", "semantic_unit_fingerprint"),
    ("semantic_coverage_items", "gate_decision_uuid"),
    ("semantic_coverage_items", "route_uuid"),
    ("semantic_coverage_items", "annotation_uuid"),
    ("semantic_coverage_items", "proposal_uuid"),
    ("semantic_candidate_links", "candidate_uuid"),
    ("semantic_candidate_links", "linked_at"),
}

_INTEGER_WP02_COLUMNS = {
    ("semantic_coverage_runs", "schema_version"),
    ("semantic_coverage_items", "raw_start"),
    ("semantic_coverage_items", "raw_end"),
    ("semantic_coverage_items", "schema_version"),
    ("semantic_candidate_links", "schema_version"),
}

_JSON_WP02_COLUMNS = {
    ("semantic_coverage_runs", "plan_json"),
    ("semantic_coverage_runs", "warnings_json"),
    ("semantic_coverage_items", "reason_codes_json"),
}

_WP02_CHECK_FRAGMENTS = {
    "sqlite": (
        "json_valid(plan_ijson)",
        "json_valid(warnings_ijson)",
        "json_valid(reason_codes_ijson)",
        "status in ('complete', 'abstain', 'retain_raw_only', 'needs_review')",
        "'durable_candidate'",
        "'unresolved_reference'",
        "raw_start >= 0 and raw_end > raw_start",
        "proposal_uuid text unique",
        "candidate_uuid = proposal_uuid",
        "default 'pr4d-route-candidate-mapper-v1'",
        "default 'pr4d-provenance-policy-v1'",
        "default 'wp02-privacy-ceiling-v1'",
    ),
    "postgres": (
        "jsonb_typeof(plan_jsonb) = 'object'",
        "jsonb_typeof(warnings_jsonb) = 'array'",
        "jsonb_typeof(reason_codes_jsonb) = 'array'",
        "status in ('complete', 'abstain', 'retain_raw_only', 'needs_review')",
        "'durable_candidate'",
        "'unresolved_reference'",
        "raw_start >= 0 and raw_end > raw_start",
        "proposal_uuid text unique",
        "candidate_uuid = proposal_uuid",
        "evidence_role in ('primary', 'secondary')",
        "support_type in ('supporting', 'contradicting')",
        "default 'pr4d-route-candidate-mapper-v1'",
        "default 'pr4d-provenance-policy-v1'",
        "default 'wp02-privacy-ceiling-v1'",
    ),
}


def sqlite_tables(migrations_dir: Path | None = None) -> set[str]:
    resolved = default_migrations_dir() if migrations_dir is None else migrations_dir
    return _tables_from_files(migration_files(resolved))


def postgres_tables(migrations_dir: Path | None = None) -> set[str]:
    return _tables_from_files(postgres_migration_files(migrations_dir))


def build_parity_report(
    sqlite_migrations_dir: Path | None = None,
    postgres_migrations_dir: Path | None = None,
) -> dict[str, Any]:
    sqlite_files = migration_files(
        default_migrations_dir() if sqlite_migrations_dir is None else sqlite_migrations_dir
    )
    postgres_files = postgres_migration_files(postgres_migrations_dir)
    sqlite = _tables_from_files(sqlite_files)
    postgres = _tables_from_files(postgres_files)
    missing_in_postgres = sorted(REQUIRED_CANONICAL_TABLES - postgres)
    missing_in_sqlite = sorted((REQUIRED_CANONICAL_TABLES - DOCUMENTED_FULL_ONLY_TABLES) - sqlite)
    contract_issues = [
        *_wp02_contract_issues(sqlite_files, dialect="sqlite"),
        *_wp02_contract_issues(postgres_files, dialect="postgres"),
    ]
    return {
        "status": (
            "pass"
            if not missing_in_postgres and not missing_in_sqlite and not contract_issues
            else "fail"
        ),
        "required_tables": sorted(REQUIRED_CANONICAL_TABLES),
        "sqlite_tables": sorted(sqlite),
        "postgres_tables": sorted(postgres),
        "missing_in_postgres": missing_in_postgres,
        "missing_in_sqlite": missing_in_sqlite,
        "contract_issues": contract_issues,
        "documented_full_only_tables": sorted(DOCUMENTED_FULL_ONLY_TABLES),
    }


def parity_report_json() -> str:
    return json.dumps(build_parity_report(), indent=2, sort_keys=True)


def _tables_from_files(files: list[Path]) -> set[str]:
    tables: set[str] = set()
    for path in files:
        tables.update(match.group("name") for match in TABLE_RE.finditer(path.read_text()))
    return tables


def _wp02_contract_issues(files: list[Path], *, dialect: str) -> list[str]:
    definitions, sql = _column_definitions(files)
    issues: list[str] = []
    for table, required_columns in WP02_REQUIRED_COLUMNS.items():
        columns = definitions.get(table, {})
        logical_columns = {_logical_column_name(name): value for name, value in columns.items()}
        for column in sorted(required_columns):
            definition = logical_columns.get(column)
            if definition is None:
                issues.append(f"{dialect}:{table}.{column}:missing")
                continue
            expected_type = (
                "JSONB"
                if dialect == "postgres" and (table, column) in _JSON_WP02_COLUMNS
                else (
                    "INTEGER"
                    if (table, column) in _INTEGER_WP02_COLUMNS
                    else (
                        "TIMESTAMPTZ"
                        if dialect == "postgres" and column.endswith("_at")
                        else "TEXT"
                    )
                )
            )
            actual_type = definition.split(maxsplit=2)[1].upper()
            if actual_type != expected_type:
                issues.append(
                    f"{dialect}:{table}.{column}:type={actual_type},expected={expected_type}"
                )
            nullable = (
                "NOT NULL" not in definition.upper() and "PRIMARY KEY" not in definition.upper()
            )
            if nullable != ((table, column) in _NULLABLE_WP02_COLUMNS):
                issues.append(f"{dialect}:{table}.{column}:nullability")
            if column == "schema_version" and not re.search(
                r"\bDEFAULT\s+1\b", definition, re.IGNORECASE
            ):
                issues.append(f"{dialect}:{table}.{column}:default")
    normalized_sql = " ".join(sql.lower().split())
    for fragment in _WP02_CHECK_FRAGMENTS[dialect]:
        if " ".join(fragment.lower().split()) not in normalized_sql:
            issues.append(f"{dialect}:constraint:{fragment}")
    return issues


def _column_definitions(files: list[Path]) -> tuple[dict[str, dict[str, str]], str]:
    sql = "\n".join(path.read_text(encoding="utf-8") for path in files)
    definitions: dict[str, dict[str, str]] = {}
    for match in TABLE_RE.finditer(sql):
        table = match.group("name")
        open_index = sql.find("(", match.end())
        close_index = _matching_parenthesis(sql, open_index)
        if open_index < 0 or close_index < 0:
            continue
        table_definitions = definitions.setdefault(table, {})
        for fragment in _split_top_level(sql[open_index + 1 : close_index]):
            cleaned = " ".join(fragment.split())
            column_match = re.match(
                r"^(?P<name>[a-zA-Z_][a-zA-Z0-9_]*)\s+"
                r"(?P<type>TEXT|INTEGER|JSONB|TIMESTAMPTZ)\b",
                cleaned,
                re.IGNORECASE,
            )
            if column_match is not None:
                table_definitions[column_match.group("name")] = cleaned
    alter_re = re.compile(
        r"ALTER\s+TABLE\s+(?P<table>[a-zA-Z_][a-zA-Z0-9_]*)\s+"
        r"ADD\s+COLUMN\s+(?:IF\s+NOT\s+EXISTS\s+)?"
        r"(?P<definition>[a-zA-Z_][a-zA-Z0-9_]*\s+"
        r"(?:TEXT|INTEGER|JSONB|TIMESTAMPTZ)\b[^;]*);",
        re.IGNORECASE | re.DOTALL,
    )
    for match in alter_re.finditer(sql):
        definition = " ".join(match.group("definition").split())
        column = definition.split(maxsplit=1)[0]
        definitions.setdefault(match.group("table"), {})[column] = definition
    return definitions, sql


def _matching_parenthesis(value: str, start: int) -> int:
    if start < 0:
        return -1
    depth = 0
    quoted = False
    for index in range(start, len(value)):
        character = value[index]
        if character == "'":
            quoted = not quoted
        elif not quoted and character == "(":
            depth += 1
        elif not quoted and character == ")":
            depth -= 1
            if depth == 0:
                return index
    return -1


def _split_top_level(value: str) -> list[str]:
    fragments: list[str] = []
    start = 0
    depth = 0
    quoted = False
    for index, character in enumerate(value):
        if character == "'":
            quoted = not quoted
        elif not quoted and character == "(":
            depth += 1
        elif not quoted and character == ")":
            depth -= 1
        elif not quoted and character == "," and depth == 0:
            fragments.append(value[start:index])
            start = index + 1
    fragments.append(value[start:])
    return fragments


def _logical_column_name(name: str) -> str:
    if name.endswith("_ijson"):
        return name.removesuffix("_ijson") + "_json"
    if name.endswith("_jsonb"):
        return name.removesuffix("_jsonb") + "_json"
    return name
