from __future__ import annotations

import argparse
import importlib
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]

SMOKES = [
    "smoke_lite",
    "smoke_full",
    "smoke_import",
    "smoke_privacy",
    "smoke_preflight",
    "heavy_import_smoke",
    "heritage_roundtrip",
    "forget_residue",
    "consistency_check",
    "recovery_tests",
    "version_consistency",
    "source_package_scan",
    "openwebui_container_smoke",
    "rc_package_schema",
]


def run_all(
    output_dir: str | Path = "release/artifacts",
    manifest_path: str | Path | None = "release/test_manifest.ijson",
    external_gates_passed: bool = False,
) -> dict[str, Any]:
    started = time.perf_counter()
    resolved_output = _resolve_repo_path(output_dir)
    manifest = _load_manifest(manifest_path)
    classifications = {item["name"]: item for item in manifest.get("tests", [])}
    results = []
    for name in SMOKES:
        module = importlib.import_module(f"release.tests.{name}")
        case_started = time.perf_counter()
        try:
            result = module.run()
            result["duration_ms"] = int((time.perf_counter() - case_started) * 1000)
        except Exception as error:
            result = {
                "name": name,
                "passed": False,
                "duration_ms": int((time.perf_counter() - case_started) * 1000),
                "failing_step": "exception",
                "remediation_hint": str(error)[:160],
            }
        classification = classifications.get(
            str(result.get("name", name)),
            classifications.get(name, {}),
        )
        result["classification"] = classification.get(
            "classification",
            result.get("classification", "placeholder"),
        )
        result["may_block_release"] = bool(classification.get("may_block_release", False))
        result["placeholder"] = result["classification"] != "real"
        result["release_gate_counted"] = (
            result["classification"] == "real" and result["may_block_release"]
        )
        results.append(result)
    blocking_results = [item for item in results if item.get("release_gate_counted")]
    report = {
        "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "duration_ms": int((time.perf_counter() - started) * 1000),
        "component_versions": _component_versions(),
        "logs_path": "release/artifacts/logs",
        "external_gates_passed": external_gates_passed,
        "passed": external_gates_passed
        and all(bool(item.get("passed")) for item in blocking_results),
        "real_gates": [
            item
            for item in manifest.get("tests", [])
            if item.get("classification") == "real" and item.get("may_block_release")
        ],
        "results": results,
    }
    write_report(resolved_output, report)
    return report


def write_report(output_dir: str | Path, report: dict[str, Any]) -> None:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    (root / "release-smoke-report.ijson").write_text(
        json.dumps(report, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    lines = [
        "# Release Gate Report",
        "",
        f"Passed: `{report['passed']}`",
        f"External gates passed: `{report['external_gates_passed']}`",
        "",
        "Placeholder smoke scripts are not release evidence.",
        "",
        "## Smoke Script Classification",
    ]
    for item in report["results"]:
        status = "SKIP" if item.get("skipped") else "PASS" if item.get("passed") else "FAIL"
        counted = "counted" if item.get("release_gate_counted") else "not-counted"
        lines.append(f"- {status} `{item['name']}` ({item['classification']}, {counted})")
    lines.extend(["", "## Real Blocking Gates"])
    for item in report["real_gates"]:
        lines.append(f"- `{item['name']}`: {item['path']}")
    (root / "release-smoke-report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _resolve_repo_path(path: str | Path) -> Path:
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate
    return REPO_ROOT / candidate


def _load_manifest(manifest_path: str | Path | None) -> dict[str, Any]:
    if manifest_path is None:
        return {"tests": []}
    path = _resolve_repo_path(manifest_path)
    if not path.exists():
        return {"tests": []}
    return json.loads(path.read_text(encoding="utf-8"))


def _component_versions() -> dict[str, str]:
    core_src = REPO_ROOT / "memorist-core" / "src"
    if str(core_src) not in sys.path:
        sys.path.insert(0, str(core_src))
    try:
        from memcore.version import SCHEMA_VERSION, __version__

        return {"memorist_core": __version__, "schema_version": str(SCHEMA_VERSION)}
    except Exception:
        return {"memorist_core": "unknown"}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="release/artifacts")
    parser.add_argument("--manifest", default="release/test_manifest.ijson")
    parser.add_argument("--external-gates-passed", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            run_all(args.out, args.manifest, args.external_gates_passed),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
