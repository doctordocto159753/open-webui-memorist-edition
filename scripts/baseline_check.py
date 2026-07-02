from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "memorist-core"
ARTIFACTS = ROOT / "release" / "artifacts"
CORE_SRC = CORE / "src"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(CORE_SRC) not in sys.path:
    sys.path.insert(0, str(CORE_SRC))


def _ensure_runtime_dependencies() -> None:
    if importlib.util.find_spec("pydantic_settings") is not None:
        return
    if os.environ.get("MEMORIST_BASELINE_CHECK_REEXEC") == "1":
        raise RuntimeError("baseline_check dependencies are unavailable after uv re-exec")
    sync = subprocess.run(
        [sys.executable, "-m", "uv", "sync", "--all-extras", "--dev"],
        cwd=CORE,
        text=True,
    )
    if sync.returncode != 0:
        raise SystemExit(sync.returncode)
    env = os.environ.copy()
    env["MEMORIST_BASELINE_CHECK_REEXEC"] = "1"
    env["MEMORIST_BASELINE_UV_PYTHON"] = sys.executable
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "uv",
            "run",
            "python",
            str(ROOT / "scripts" / "baseline_check.py"),
            *sys.argv[1:],
        ],
        cwd=CORE,
        text=True,
        env=env,
    )
    raise SystemExit(completed.returncode)


_ensure_runtime_dependencies()

from release.scan_source_tree import scan_path as scan_source_tree  # noqa: E402
from scripts.clean_artifacts import collect_artifacts  # noqa: E402

from memcore.model_control.roles import list_role_specs  # noqa: E402
from memcore.version import SCHEMA_VERSION, __version__  # noqa: E402

REQUIRED_MAKE_TARGETS = {
    "check",
    "test",
    "lint",
    "typecheck",
    "openwebui-contract-tests",
    "model-control-tests",
    "memory-worker-prompt-pack-test",
    "smoke-daily",
    "smoke-import-heavy-ci",
    "heritage-roundtrip",
    "forget-residue",
    "consistency-check",
    "recovery-tests",
    "source-tree-scan",
    "source-package",
    "assemble-rc",
    "rc-schema-test",
    "version-consistency",
    "baseline-check",
    "clean-artifacts",
}

SOURCE_ZIP_REL = "release/source/open-webui-memorist-edition-source.zip"
RC_ZIP_REL = "release/rc/memorist-openwebui-0.2.0-beta.1.zip"
MEMORY_WORKER_PATH = "memorist-core/src/memcore/memory_worker"
PROMPTS_PATH = f"{MEMORY_WORKER_PATH}/prompts"
PROMPT_REGISTRY_PATH = f"{PROMPTS_PATH}/registry.py"
PROMPT_SCHEMAS_PATH = f"{PROMPTS_PATH}/schemas.py"
PROMPT_SYSTEM_PATH = f"{PROMPTS_PATH}/system"
MODEL_CONTROL_MODEL_PATH = "memorist-core/src/memcore/models/model_control.py"
MODEL_CONTROL_PATH = "memorist-core/src/memcore/model_control"
CANONICAL_STORE_PATH = "memorist-core/src/memcore/storage/canonical"
POSTGRES_STORE_PATH = "memorist-core/src/memcore/storage/postgres"
SCHEDULER_PATH = "memorist-core/src/memcore/scheduler"
GRAPH_PATH = "memorist-core/src/memcore/graph"
FULL_GRAPH_EVIDENCE = f"{GRAPH_PATH}; docker-compose.full.yml"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-gates", action="store_true", help="Only write audit artifacts.")
    args = parser.parse_args(argv)

    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    write_baseline_audit()
    write_gap_matrix()
    if args.skip_gates:
        return 0
    report = run_baseline_gates()
    write_check_report(report)
    return 0 if report["passed"] else 1


def run_baseline_gates() -> dict[str, Any]:
    uv = _uv_command
    commands = [
        _cmd("core_sync", uv("sync", "--all-extras", "--dev"), CORE),
        _cmd("core_ruff", uv("run", "ruff", "check", "."), CORE),
        _cmd("core_mypy", uv("run", "mypy", "src/memcore"), CORE),
        _cmd("core_pytest", uv("run", "pytest", "-q"), CORE),
        _cmd(
            "model_control_tests",
            uv("run", "pytest", "-q", "tests/test_model_control_plane.py"),
            CORE,
        ),
        _cmd(
            "prompt_pack_tests",
            uv("run", "pytest", "-q", "tests/test_memory_worker_prompt_pack.py"),
            CORE,
        ),
        _cmd(
            "openwebui_contract_tests",
            uv("run", "pytest", "-q", "../open-webui-integration/memorist/tests"),
            CORE,
        ),
        _cmd(
            "daily_use_smoke",
            uv("run", "python", "../release/tests/daily_use_smoke.py"),
            CORE,
        ),
        _cmd(
            "heavy_import_ci_small",
            uv(
                "run",
                "python",
                "../release/tests/heavy_import_smoke.py",
                "--mode",
                "ci-small",
            ),
            CORE,
        ),
        _cmd(
            "heritage_roundtrip",
            uv("run", "python", "../release/tests/heritage_roundtrip.py"),
            CORE,
        ),
        _cmd(
            "forget_residue",
            uv("run", "python", "../release/tests/forget_residue.py"),
            CORE,
        ),
        _cmd(
            "consistency_check",
            uv("run", "python", "../release/tests/consistency_check.py"),
            CORE,
        ),
        _cmd(
            "recovery_tests",
            uv("run", "python", "../release/tests/recovery_tests.py"),
            CORE,
        ),
        _cmd(
            "version_consistency",
            uv("run", "python", "../release/tests/version_consistency.py"),
            CORE,
        ),
        _cmd(
            "assemble_rc",
            [sys.executable, "installer/scripts/assemble_rc.py"],
            ROOT,
        ),
        _cmd(
            "rc_package_schema",
            uv("run", "python", "../release/tests/rc_package_schema.py"),
            CORE,
        ),
        _cmd(
            "rc_forbidden_scan",
            [
                sys.executable,
                "-m",
                "release.scan_forbidden_files",
                RC_ZIP_REL,
            ],
            ROOT,
        ),
        _cmd(
            "source_package",
            [
                sys.executable,
                "release/source_package.py",
                "--out",
                SOURCE_ZIP_REL,
            ],
            ROOT,
        ),
        _cmd(
            "source_package_scan",
            [
                sys.executable,
                "-m",
                "release.scan_source_tree",
                SOURCE_ZIP_REL,
            ],
            ROOT,
        ),
        _cmd(
            "root_cleanup_apply",
            [_host_python(), "scripts/clean_artifacts.py", "--apply", "--keep-core-venv"],
            ROOT,
        ),
        _cmd(
            "root_source_tree_scan",
            [_host_python(), "-B", "scripts/scan_source_tree.py", "--allow-active-venv"],
            ROOT,
        ),
    ]
    results = [run_command(item) for item in commands]
    results.extend(
        [
            run_internal_check("documentation_consistency", documentation_consistency_check),
            run_internal_check("makefile_target_check", makefile_target_check),
        ]
    )
    return {
        "created_at": now_z(),
        "component_versions": {
            "memorist_core": __version__,
            "schema_version": SCHEMA_VERSION,
        },
        "passed": all(item["passed"] for item in results),
        "placeholder_counted_as_real": False,
        "manual_only_gates": [
            {
                "name": "full_external_container_smoke",
                "status": "manual-only",
                "reason": (
                    "Docker/FalkorDB/PostgreSQL external runtime is not required for "
                    "Lite baseline."
                ),
            }
        ],
        "results": results,
    }


def write_baseline_audit() -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as temp_dir:
        os.environ["MEMORIST_DB_PATH"] = str(Path(temp_dir) / "audit.sqlite")
        os.environ["MEMORIST_OBJECT_STORE_PATH"] = str(Path(temp_dir) / "objects")
        from memcore.main import create_app
        from memcore.memory_worker.prompts.registry import list_prompts

        routes = sorted(
            {
                f"{','.join(sorted(route.methods or []))} {route.path}"
                for route in create_app().routes
                if getattr(route, "path", "").startswith("/memcore")
            }
        )

    migration_list = [path.name for path in sorted((CORE / "migrations").glob("*.sql"))]
    test_list = [path.name for path in sorted((CORE / "tests").glob("test_*.py"))]
    manifest = json.loads((ROOT / "release" / "test_manifest.ijson").read_text(encoding="utf-8"))
    dirty_artifacts = collect_artifacts(ROOT)
    source_zip = ROOT / SOURCE_ZIP_REL
    rc_zip = ROOT / RC_ZIP_REL
    source_package_issues = scan_source_tree(source_zip) if source_zip.exists() else []

    report = {
        "created_at": now_z(),
        "project_version": __version__,
        "schema_version": SCHEMA_VERSION,
        "migration_list": migration_list,
        "test_suite_list": test_list,
        "api_route_list": routes,
        "prompt_registry_list": [
            {
                "prompt_id": prompt.prompt_id,
                "prompt_version": prompt.prompt_version,
                "model_role": prompt.model_role.value,
                "lifecycle": prompt.lifecycle,
            }
            for prompt in list_prompts()
        ],
        "model_control_roles": list_role_specs(),
        "release_package_paths": {
            "rc_zip": str(rc_zip),
            "rc_sha256": str(rc_zip.with_suffix(rc_zip.suffix + ".sha256")),
            "source_zip": str(source_zip),
        },
        "source_package_state": {
            "exists": source_zip.exists(),
            "scan_passed": source_zip.exists() and not source_package_issues,
            "issues": [issue.__dict__ for issue in source_package_issues],
        },
        "known_placeholders": [
            item for item in manifest["tests"] if item.get("classification") == "placeholder"
        ],
        "known_manual_only_tests": [
            item
            for item in manifest["tests"]
            if item.get("classification") in {"manual-only", "manual-real"}
        ],
        "known_experimental_modules": [
            {
                "name": "Full graph compose profile",
                "status": "experimental",
                "path": "docker-compose.full.yml",
            },
            {
                "name": "FalkorDB graph projection extension point",
                "status": "experimental",
                "path": "memorist-core/src/memcore/graph",
            },
        ],
        "known_stale_docs": find_stale_doc_claims(),
        "known_dirty_root_artifacts": [item.__dict__ for item in dirty_artifacts],
        "classification_summary": {
            "implemented": [
                "SQLite Lite runtime",
                "Open WebUI Filter/Function integration contract",
                "Memory Intelligence Core / sentence-level Jakobson pipeline",
                "Model Control Plane backend/runtime baseline",
                "Memory Worker Prompt Pack v2 contract baseline",
                "Heavy import ci-small smoke",
                "Heritage roundtrip",
                "Forget residue checks",
                "Consistency and recovery checks",
            ],
            "scaffolded": [
                "FalkorDB graph projection extension point",
                "Full compose profile and PostgreSQL/FalkorDB preview path",
            ],
            "missing": [
                "Full external smoke certification",
                "Graph retrieval and graph forget-residue evidence",
                "Fully automated Open WebUI container smoke with Filter install",
            ],
        },
    }
    write_json(ARTIFACTS / "baseline-audit-report.ijson", report)
    write_markdown(ARTIFACTS / "baseline-audit-report.md", render_audit_markdown(report))
    return report


def write_gap_matrix() -> dict[str, Any]:
    matrix = {
        "created_at": now_z(),
        "summary": (
            "Steps 1-4 are implemented as a development baseline; Full external "
            "certification remains experimental/manual."
        ),
        "steps": [
            {
                "step": "Step 1",
                "name": "Memory Intelligence Core / Sentence-Level Jakobson Pipeline",
                "status": "implemented-baseline",
                "preserve": ["existing text_units offsets", "candidate/evidence lineage"],
                "replace_or_deprecate": ["memorist.unit_analysis as primary linguistic prompt"],
                "delete": [],
                "checks": [
                    _gap(
                        "deterministic sentence segmentation",
                        "present",
                        MEMORY_WORKER_PATH,
                    ),
                    _gap(
                        "official memorist.jakobson_sentence_analysis prompt",
                        "present-v2",
                        PROMPTS_PATH,
                    ),
                    _gap(
                        "six-factor Jakobson output schema",
                        "present",
                        PROMPT_SCHEMAS_PATH,
                    ),
                    _gap("jakobson_analysis_runs table", "present", "memorist-core/migrations"),
                    _gap("memory_signal_routes table", "present", "memorist-core/migrations"),
                    _gap(
                        "annotation_uuid/route_uuid lineage in candidates",
                        "present",
                        "memorist-core/src/memcore/models",
                    ),
                    _gap(
                        "legacy unit_analysis marked derived/legacy",
                        "present",
                        "docs/concept-glossary.md",
                    ),
                ],
            },
            {
                "step": "Step 2",
                "name": "Full Mode Storage/Core Runtime",
                "status": "experimental-preview",
                "preserve": ["SQLite Lite runtime", "local-first config", "FalkorDB disabled mode"],
                "replace_or_deprecate": ["ambiguous Full mode wording"],
                "delete": [],
                "checks": [
                    _gap(
                        "runtime profile split lite/full",
                        "present",
                        ".env.example; docker-compose.lite.yml; docker-compose.full.yml",
                    ),
                    _gap("PostgreSQL canonical store", "preview", CANONICAL_STORE_PATH),
                    _gap("CanonicalStore abstraction", "present", CANONICAL_STORE_PATH),
                    _gap("durable jobs/outbox in PostgreSQL", "preview", POSTGRES_STORE_PATH),
                    _gap("in-memory hot scheduler", "present", SCHEDULER_PATH),
                    _gap(
                        "FalkorDB projection from canonical store",
                        "experimental",
                        FULL_GRAPH_EVIDENCE,
                    ),
                    _gap(
                        "Full compose smoke",
                        "manual-only",
                        "release/tests/openwebui_container_smoke.py",
                    ),
                ],
            },
            {
                "step": "Step 3",
                "name": "Model Control Plane Runtime Integration",
                "status": "implemented-backend-baseline",
                "preserve": [
                    "role specs",
                    "profile CRUD",
                    "usage events",
                    "privacy acknowledgement",
                ],
                "replace_or_deprecate": [
                    "scaffold-only lifecycle wording after runtime integration"
                ],
                "delete": [],
                "checks": [
                    _gap("main_chat_observed role", "present", MODEL_CONTROL_MODEL_PATH),
                    _gap("preflight role", "present", MODEL_CONTROL_MODEL_PATH),
                    _gap("memory_extraction role", "present", MODEL_CONTROL_MODEL_PATH),
                    _gap("embedding role", "present", MODEL_CONTROL_MODEL_PATH),
                    _gap("provider abstraction", "present", MODEL_CONTROL_PATH),
                    _gap(
                        "preflight lifecycle enforcement",
                        "present",
                        "memorist-core/src/memcore/preflight.py",
                    ),
                    _gap("extraction async lifecycle", "present", MEMORY_WORKER_PATH),
                ],
            },
            {
                "step": "Step 4",
                "name": "Memory Worker Prompt Pack v2",
                "status": "implemented-contract-baseline",
                "preserve": [
                    "versioned registry",
                    "I-JSON output validation",
                    "prompt audit tables",
                ],
                "replace_or_deprecate": ["Prompt Pack v1 as final target"],
                "delete": [],
                "checks": [
                    _gap("versioned prompt registry", "present-v2", PROMPTS_PATH),
                    _gap(
                        "prompt execution audit table",
                        "present",
                        "memorist-core/migrations/0018_prompt_pack_v2_audit.sql",
                    ),
                    _gap("official Jakobson prompt", "present-v2", PROMPT_SYSTEM_PATH),
                    _gap(
                        "route-specific extractor prompts",
                        "present-v2",
                        PROMPT_SYSTEM_PATH,
                    ),
                    _gap("preflight planning prompt", "present-v2", PROMPT_REGISTRY_PATH),
                    _gap("privacy sensitivity prompt", "present-v2", PROMPT_REGISTRY_PATH),
                    _gap("role-to-prompt mapping", "present", PROMPT_REGISTRY_PATH),
                ],
            },
        ],
    }
    write_json(ARTIFACTS / "final-four-gap-matrix.ijson", matrix)
    write_markdown(ARTIFACTS / "final-four-gap-matrix.md", render_gap_markdown(matrix))
    return matrix


def run_command(command: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            command["argv"],
            cwd=command["cwd"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=command.get("timeout_seconds", 300),
        )
        output = completed.stdout[-4000:]
        return {
            **command,
            "passed": completed.returncode == 0,
            "returncode": completed.returncode,
            "duration_ms": int((time.perf_counter() - started) * 1000),
            "output_tail": output,
        }
    except Exception as error:
        return {
            **command,
            "passed": False,
            "returncode": None,
            "duration_ms": int((time.perf_counter() - started) * 1000),
            "output_tail": str(error),
        }


def run_internal_check(name: str, check: Any) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        issues = check()
        return {
            "name": name,
            "argv": [],
            "command": name,
            "cwd": str(ROOT),
            "timeout_seconds": 0,
            "passed": not issues,
            "returncode": 0 if not issues else 1,
            "duration_ms": int((time.perf_counter() - started) * 1000),
            "output_tail": "OK" if not issues else "\n".join(issues[:50]),
        }
    except Exception as error:
        return {
            "name": name,
            "argv": [],
            "command": name,
            "cwd": str(ROOT),
            "timeout_seconds": 0,
            "passed": False,
            "returncode": 1,
            "duration_ms": int((time.perf_counter() - started) * 1000),
            "output_tail": str(error),
        }


def _cmd(name: str, argv: list[str], cwd: Path, timeout_seconds: int = 300) -> dict[str, Any]:
    return {
        "name": name,
        "argv": argv,
        "command": " ".join(argv),
        "cwd": str(cwd),
        "timeout_seconds": timeout_seconds,
    }


def _uv_command(*args: str) -> list[str]:
    return [_host_python(), "-m", "uv", *args]


def _host_python() -> str:
    return os.environ.get("MEMORIST_BASELINE_UV_PYTHON") or getattr(
        sys,
        "_base_executable",
        sys.executable,
    )


def _gap(name: str, status: str, evidence: str) -> dict[str, str]:
    return {"name": name, "status": status, "evidence": evidence}


def find_stale_doc_claims() -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    for doc in _current_documentation_paths():
        text = doc.read_text(encoding="utf-8")
        for needle, reason in _stale_documentation_needles().items():
            if needle in text:
                findings.append({"path": doc.relative_to(ROOT).as_posix(), "reason": reason})
    return findings


def documentation_consistency_check() -> list[str]:
    stale_needles = _stale_documentation_needles()
    required_needles = [
        "Lite Mode: beta-candidate",
        "Full Mode: experimental preview",
        "Prompt Pack v2",
        "Jakobson Pipeline",
        "Model Control Plane",
        "contract-tested",
        "pinned container smoke pending",
    ]
    current_docs = _current_documentation_paths()
    skipped_historical_docs = _historical_documentation_paths()
    issues: list[str] = []
    aggregate_text_parts: list[str] = []
    for doc in current_docs:
        text = doc.read_text(encoding="utf-8")
        aggregate_text_parts.append(text)
        for needle, expected in stale_needles.items():
            if needle in text:
                issues.append(f"{doc.relative_to(ROOT).as_posix()}: {needle} -> {expected}")
    aggregate_text = "\n".join(aggregate_text_parts)
    for needle in required_needles:
        if needle not in aggregate_text:
            issues.append(f"required current phrase missing from active docs: {needle}")
    _write_documentation_consistency_report(
        current_docs=current_docs,
        skipped_historical_docs=skipped_historical_docs,
        issues=issues,
    )
    return issues


def _stale_documentation_needles() -> dict[str, str]:
    return {
        "Prompt Pack v2 remains planned": "Prompt Pack v2 is implemented as contract baseline",
        "Prompt Pack v1 is current baseline": "Prompt Pack v2 is current baseline",
        "Prompt Pack v2 is planned": "Prompt Pack v2 is implemented as contract baseline",
        "Prompt Pack v2 is a future Step 4": "Prompt Pack v2 is implemented",
        "future Step 4 deliverable": "Prompt Pack v2 is implemented",
        "Step 3 will be deepened": "Model Control Plane backend/runtime is implemented",
        "memory model is intentionally not implemented": "memory model baseline is implemented",
        "not implemented in Phase 1": "historical-only phrase",
        "v0.1.0-alpha.1 local alpha RC": "stale alpha label",
        "v0.1.0-alpha.1": "stale alpha version reference",
        "Step 0 baseline candidate": "current baseline includes Steps 1-4",
        (
            "sentence-level Jakobson memory intelligence, deeper runtime Model Control "
            "integration, and Prompt Pack v2 are planned"
        ): "Steps 1, 3, and 4 are implemented baselines",
        "Full Mode is stable": "Full Mode must remain experimental preview",
        "Full Mode beta-supported": "Full Mode remains experimental preview",
        "Open WebUI container smoke passed": "pinned container smoke remains pending/manual",
        "pinned container smoke passed": "pinned container smoke remains pending/manual",
    }


def _primary_documentation_paths() -> list[Path]:
    return [
        ROOT / "README.md",
        ROOT / "README.fa.md",
        ROOT / "GITHUB_BASELINE.md",
        ROOT / "GITHUB_BASELINE.fa.md",
        ROOT / "RELEASE_NOTES.md",
        ROOT / "RELEASE_DECISION.md",
        ROOT / "HANDOFF.md",
        ROOT / "KNOWN_LIMITATIONS.md",
        CORE / "README.md",
        ROOT / "release" / "rc" / "RELEASE_NOTES.md",
        ROOT / "release" / "rc" / "RELEASE_DECISION.md",
        ROOT / "release" / "rc" / "HANDOFF.md",
        ROOT / "release" / "memorist-openwebui" / "README.md",
    ]


def _current_documentation_paths() -> list[Path]:
    paths: set[Path] = {path for path in _primary_documentation_paths() if path.exists()}
    docs_root = ROOT / "docs"
    if docs_root.exists():
        for path in docs_root.glob("*.md"):
            paths.add(path)
    return sorted(paths, key=lambda item: item.relative_to(ROOT).as_posix())


def _historical_documentation_paths() -> list[Path]:
    historical_root = ROOT / "docs" / "historical"
    if not historical_root.exists():
        return []
    return sorted(historical_root.rglob("*.md"), key=lambda item: item.relative_to(ROOT).as_posix())


def _write_documentation_consistency_report(
    *,
    current_docs: list[Path],
    skipped_historical_docs: list[Path],
    issues: list[str],
) -> None:
    payload = {
        "created_at": now_z(),
        "passed": not issues,
        "scanned_docs": [path.relative_to(ROOT).as_posix() for path in current_docs],
        "skipped_historical_docs": [
            path.relative_to(ROOT).as_posix() for path in skipped_historical_docs
        ],
        "issues": issues,
    }
    write_json(ARTIFACTS / "documentation-consistency-report.ijson", payload)
    lines = [
        "# Documentation Consistency Report",
        "",
        f"- Passed: `{payload['passed']}`",
        "",
        "## Scanned Docs",
    ]
    lines.extend(f"- `{path}`" for path in payload["scanned_docs"])
    lines.extend(["", "## Skipped Historical Docs"])
    lines.extend(f"- `{path}`" for path in payload["skipped_historical_docs"])
    lines.extend(["", "## Issues"])
    lines.extend(f"- {issue}" for issue in issues) if issues else lines.append("- _none_")
    write_markdown(ARTIFACTS / "documentation-consistency-report.md", "\n".join(lines) + "\n")


def makefile_target_check() -> list[str]:
    makefile = ROOT / "Makefile"
    if not makefile.exists():
        return ["Makefile missing"]
    text = makefile.read_text(encoding="utf-8")
    targets = {
        line.split(":", 1)[0]
        for line in text.splitlines()
        if line and not line.startswith(("\t", " ", ".", "#")) and ":" in line
    }
    issues = [
        f"missing Makefile target: {target}"
        for target in sorted(REQUIRED_MAKE_TARGETS - targets)
    ]
    required_paths = [
        "scripts/baseline_check.py",
        "scripts/clean_artifacts.py",
        "scripts/scan_source_tree.py",
        "release/source_package.py",
        "installer/scripts/assemble_rc.py",
        "release/tests/rc_package_schema.py",
        "release/tests/version_consistency.py",
        "release/tests/daily_use_smoke.py",
        "release/tests/heavy_import_smoke.py",
        "release/tests/heritage_roundtrip.py",
        "release/tests/forget_residue.py",
        "release/tests/consistency_check.py",
        "release/tests/recovery_tests.py",
    ]
    for path in required_paths:
        if not (ROOT / path).exists():
            issues.append(f"Makefile referenced path missing or required path absent: {path}")
    return issues


def write_check_report(report: dict[str, Any]) -> None:
    write_json(ARTIFACTS / "baseline-check-report.ijson", report)
    lines = [
        "# Baseline Check Report",
        "",
        f"Created: `{report['created_at']}`",
        f"Passed: `{report['passed']}`",
        "",
        "| Gate | Result | Duration ms |",
        "| --- | --- | ---: |",
    ]
    for result in report["results"]:
        status = "PASS" if result["passed"] else "FAIL"
        lines.append(f"| `{result['name']}` | {status} | {result['duration_ms']} |")
    write_markdown(ARTIFACTS / "baseline-check-report.md", "\n".join(lines) + "\n")


def render_audit_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Baseline Audit Report",
        "",
        f"- Project version: `{report['project_version']}`",
        f"- Schema version: `{report['schema_version']}`",
        f"- Migrations: `{len(report['migration_list'])}`",
        f"- Core tests: `{len(report['test_suite_list'])}`",
        f"- API routes: `{len(report['api_route_list'])}`",
        f"- Prompt registry entries: `{len(report['prompt_registry_list'])}`",
        "",
        "## Classification",
    ]
    for key, values in report["classification_summary"].items():
        lines.append(f"### {key.title()}")
        lines.extend(f"- {value}" for value in values)
    lines.extend(["", "## Dirty Root Artifacts"])
    for artifact in report["known_dirty_root_artifacts"][:100]:
        safe_to_delete = artifact.get("safe_to_delete", artifact.get("removable", False))
        marker = "safe_to_delete" if safe_to_delete else "retained"
        lines.append(f"- `{artifact['path']}` ({marker}; {artifact['reason']})")
    lines.extend(["", "## Manual/Placeholder Evidence"])
    for item in report["known_placeholders"]:
        lines.append(f"- Placeholder: `{item['name']}`")
    for item in report["known_manual_only_tests"]:
        lines.append(f"- Manual-only/manual-real: `{item['name']}`")
    return "\n".join(lines) + "\n"


def render_gap_markdown(matrix: dict[str, Any]) -> str:
    lines = ["# Final Four Gap Matrix", "", matrix["summary"], ""]
    for step in matrix["steps"]:
        lines.extend([f"## {step['step']} — {step['name']}", "", f"Status: `{step['status']}`"])
        lines.extend(["", "### Checks"])
        for check in step["checks"]:
            evidence = f" — {check['evidence']}" if check["evidence"] else ""
            lines.append(f"- `{check['status']}` {check['name']}{evidence}")
        lines.extend(["", "### Preserve"])
        lines.extend(f"- {item}" for item in step["preserve"])
        lines.extend(["", "### Replace Or Deprecate"])
        lines.extend(f"- {item}" for item in step["replace_or_deprecate"])
        lines.append("")
    return "\n".join(lines)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )


def write_markdown(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def now_z() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


if __name__ == "__main__":
    raise SystemExit(main())
