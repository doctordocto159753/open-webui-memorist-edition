from __future__ import annotations

import re
from pathlib import Path
from typing import Any

EXECUTE_PATTERN = re.compile(r"\b(execute|executemany|executescript)\s*\(")
SQL_WRITE_PATTERN = re.compile(r"\b(INSERT|UPDATE|DELETE|REPLACE)\b", re.IGNORECASE)

ALLOWED_DIRECT_WRITE_PATHS = {
    "memcore/repositories/domain.py": (
        "domain repositories are the bounded write API used inside the actor"
    ),
    "memcore/repositories/sqlite.py": (
        "generic repository validates _ijson fields before insert/update"
    ),
    "memcore/active_memory/repositories.py": (
        "active-memory repository writes are bounded block metadata operations"
    ),
    "memcore/repositories/retrieval.py": (
        "retrieval repository writes are bounded query/candidate audit rows"
    ),
    "memcore/governance/correction.py": (
        "correction writes are bounded governance state transitions"
    ),
    "memcore/openwebui/commands.py": "Open WebUI capture runs inside the write actor",
    "memcore/openwebui/session_resolution.py": (
        "session resolution is a short control-plane upsert"
    ),
    "memcore/prompt_budget/model_context.py": (
        "model registry writes are local static model metadata upserts"
    ),
    "memcore/model_control/repository.py": (
        "model-control writes are bounded local role/profile/default/usage metadata operations"
    ),
    "memcore/api/routes_retrieval.py": "retrieval route creates bounded read-run audit rows",
    "memcore/security/reports.py": "security report writes bounded local audit reports",
    "memcore/reliability/storage_maintenance.py": (
        "maintenance writes only SQLite checkpoint/vacuum metadata"
    ),
    "memcore/storage/write_actor.py": "single writer owns idempotency bookkeeping writes",
    "memcore/storage/migrations.py": "migration runner applies versioned schema files",
    "memcore/storage/postgres/migrations.py": (
        "PostgreSQL migration runner applies versioned Full-mode schema files"
    ),
    "memcore/storage/postgres/jobs.py": (
        "PostgreSQL job SQL constants are durable Full-mode scheduler primitives"
    ),
    "memcore/storage/postgres/outbox.py": (
        "PostgreSQL outbox SQL constants are durable Full-mode projection primitives"
    ),
    "memcore/graph/service.py": (
        "Full graph rebuild writes bounded projection_state rows after FalkorDB projection"
    ),
    "memcore/storage/direct_write_audit.py": (
        "static audit utility contains SQL keywords only as data"
    ),
    "memcore/imports/repositories.py": "staging metadata writes are short-lived",
    "memcore/imports/commit_batches.py": (
        "import commit batch records are bounded low-priority actor batch metadata"
    ),
    "memcore/imports/service.py": (
        "batch commit internals run under ImportBatchCommitCommand"
    ),
    "memcore/imports/progress.py": "import progress writes are small status updates",
    "memcore/imports/scheduler.py": "pause/resume/cancel are bounded control-plane updates",
    "memcore/governance/privacy.py": (
        "privacy mutation internals run under PrivacyRequestCommand"
    ),
    "memcore/reliability/backup.py": "SQLite backup API does not mutate canonical content",
    "memcore/reliability/consistency/repair.py": (
        "safe repair only marks stale operational rows or rebuilds FTS"
    ),
    "memcore/reliability/recovery.py": "recovery only marks interrupted operations resumable/dead",
    "memcore/retrieval/lexical.py": (
        "FTS maintenance is a local index projection"
    ),
    "memcore/heritage/package.py": "restore internals can run inside HeritageRestoreCommand",
}


def audit_direct_writes(src_root: str | Path) -> dict[str, Any]:
    root = Path(src_root)
    findings: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*.py")):
        relative = path.relative_to(root).as_posix()
        write_statement_count = _write_statement_count(path)
        if not write_statement_count:
            continue
        allowance = ALLOWED_DIRECT_WRITE_PATHS.get(relative)
        findings.append(
            {
                "path": relative,
                "write_statement_count": write_statement_count,
                "allowed": allowance is not None,
                "justification": allowance,
            }
        )
    unexpected = [finding for finding in findings if not finding["allowed"]]
    return {
        "status": "ok" if not unexpected else "unexpected_direct_writes",
        "unexpected_count": len(unexpected),
        "findings": findings,
        "unexpected": unexpected,
    }


def _write_statement_count(path: Path) -> int:
    lines = path.read_text(encoding="utf-8").splitlines()
    count = 0
    for index, line in enumerate(lines):
        if not EXECUTE_PATTERN.search(line):
            continue
        window = "\n".join(lines[index : index + 5])
        if SQL_WRITE_PATTERN.search(window):
            count += 1
    return count
