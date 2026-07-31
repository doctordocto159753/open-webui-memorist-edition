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
    "memcore/active_memory/compaction/compactor.py": (
        "compaction marks a rejected stage result inside the same fenced write boundary"
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
    "memcore/api/routes_memory.py": (
        "Full-mode processing endpoints use explicit PostgreSQL transactions; "
        "Lite writes are delegated to repositories/pipelines"
    ),
    "memcore/api/routes_memory_control.py": (
        "memory-control mutations are bounded policy transitions"
    ),
    "memcore/api/routes_openwebui.py": (
        "Open WebUI handlers execute short commands through the write gateway or "
        "explicit Full-mode transactions"
    ),
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
    "memcore/imports/processing.py": (
        "import reconstruction persists bounded stage and prompt audit records "
        "inside the import worker transaction"
    ),
    "memcore/imports/commit_batches.py": (
        "import commit batch records are bounded low-priority actor batch metadata"
    ),
    "memcore/imports/service.py": ("batch commit internals run under ImportBatchCommitCommand"),
    "memcore/imports/progress.py": "import progress writes are small status updates",
    "memcore/imports/scheduler.py": "pause/resume/cancel are bounded control-plane updates",
    "memcore/governance/privacy.py": ("privacy mutation internals run under PrivacyRequestCommand"),
    "memcore/reliability/backup.py": "SQLite backup API does not mutate canonical content",
    "memcore/reliability/consistency/repair.py": (
        "safe repair only marks stale operational rows or rebuilds FTS"
    ),
    "memcore/reliability/recovery.py": "recovery only marks interrupted operations resumable/dead",
    "memcore/retrieval/lexical.py": ("FTS maintenance is a local index projection"),
    "memcore/heritage/package.py": "restore internals can run inside HeritageRestoreCommand",
    "memcore/memory_control/full_retrieval.py": (
        "Full-mode retrieval writes bounded PostgreSQL run/candidate/attachment audit rows"
    ),
    "memcore/memory_control/repository.py": (
        "memory-control repository owns bounded policy and turn-state writes"
    ),
    "memcore/memory_worker/pipeline.py": (
        "Lite memory worker owns one serialized processing transaction"
    ),
    "memcore/memory_worker/postgres/pipeline.py": (
        "Full memory worker owns explicit short PostgreSQL processing transactions"
    ),
    "memcore/memory_worker/postgres/routing_policy_adapter.py": (
        "Full routing adapter persists one bounded canonical routing decision"
    ),
    "memcore/memory_worker/service.py": (
        "memory job service owns serialized Lite job claims and lifecycle transitions"
    ),
    "memcore/model_control/postgres_repository.py": (
        "PostgreSQL model-control repository owns bounded profile/default/usage writes"
    ),
    "memcore/model_control/stage_invocation.py": (
        "shared stage boundary persists idempotent execution and prompt audit rows"
    ),
    "memcore/security/actor.py": (
        "actor middleware writes bounded nonce records for replay protection"
    ),
    "memcore/memory_worker/attempt_audit.py": (
        "append-only provider attempt reservations must survive the same fenced "
        "boundary that guards the paid call"
    ),
    "memcore/memory_worker/message_semantics.py": (
        "message semantic records are written from deterministic UUIDv5 identities "
        "inside the worker's processing transaction"
    ),
    "memcore/memory_worker/semantic/runtime_adapters.py": (
        "shared WP02 orchestration adapters own the SQLite/PostgreSQL mechanics for "
        "one serialized semantic stage"
    ),
    "memcore/memory_worker/postgres/semantic_coverage.py": (
        "PostgreSQL coverage/replay persistence runs inside the Full worker transaction"
    ),
    "memcore/repositories/semantic_coverage.py": (
        "SQLite coverage commands are executed through the single write actor"
    ),
    "memcore/preflight.py": (
        "preflight persists one bounded audited retrieval plan row for the run it owns"
    ),
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
