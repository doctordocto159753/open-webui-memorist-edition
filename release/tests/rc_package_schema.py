from __future__ import annotations

import hashlib
import json
import sys
import zipfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
CORE_SRC = ROOT / "memorist-core" / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(CORE_SRC) not in sys.path:
    sys.path.insert(0, str(CORE_SRC))

from memcore.version import SCHEMA_VERSION  # noqa: E402
from release.scan_forbidden_files import scan_path  # noqa: E402

ZIP_PATH = ROOT / "release" / "rc" / "memorist-openwebui-0.2.0-beta.1.zip"
SHA_PATH = ROOT / "release" / "rc" / "memorist-openwebui-0.2.0-beta.1.sha256"
VERSION_SUFFIX = "release/memorist-openwebui/VERSION.ijson"
MANIFEST_SUFFIX = "release/package-manifest.ijson"


def run() -> dict[str, Any]:
    try:
        result = validate_rc_package()
        remediation_hint = None
        if not result["valid"]:
            remediation_hint = "Run `make assemble-rc rc-schema-test`."
        return {
            "name": "rc_zip_schema_regression",
            "passed": result["valid"],
            "classification": "real",
            "failing_step": None if result["valid"] else "rc_zip_schema_regression",
            "remediation_hint": remediation_hint,
            **result,
        }
    except Exception as error:
        return {
            "name": "rc_zip_schema_regression",
            "passed": False,
            "classification": "real",
            "failing_step": "exception",
            "remediation_hint": str(error)[:160],
        }


def main() -> int:
    result = run()
    print(json.dumps(result, indent=2))
    return 0 if result["passed"] else 1


def validate_rc_package() -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    if not ZIP_PATH.exists():
        issues.append({"issue_code": "missing_zip", "path": str(ZIP_PATH)})
    if not SHA_PATH.exists():
        issues.append({"issue_code": "missing_checksum", "path": str(SHA_PATH)})
    if issues:
        return {"valid": False, "issues": issues}

    forbidden = scan_path(ZIP_PATH)
    issues.extend(
        {"issue_code": "forbidden_file", "path": issue.path, "reason": issue.reason}
        for issue in forbidden
    )

    with zipfile.ZipFile(ZIP_PATH) as package:
        names = package.namelist()
        version_name = _find_suffix(names, VERSION_SUFFIX)
        manifest_name = _find_suffix(names, MANIFEST_SUFFIX)
        if version_name is None:
            issues.append({"issue_code": "missing_version_ijson", "suffix": VERSION_SUFFIX})
            version_payload: dict[str, Any] = {}
        else:
            version_payload = json.loads(package.read(version_name).decode("utf-8"))
        if manifest_name is None:
            issues.append({"issue_code": "missing_package_manifest", "suffix": MANIFEST_SUFFIX})
        schema_version = version_payload.get("schema_version")
        if schema_version != SCHEMA_VERSION:
            issues.append(
                {
                    "issue_code": "schema_version_mismatch",
                    "zip_schema_version": schema_version,
                    "source_schema_version": SCHEMA_VERSION,
                }
            )

    expected_digest = SHA_PATH.read_text(encoding="utf-8").split()[0]
    actual_digest = hashlib.sha256(ZIP_PATH.read_bytes()).hexdigest()
    if expected_digest != actual_digest:
        issues.append(
            {
                "issue_code": "checksum_mismatch",
                "expected": expected_digest,
                "actual": actual_digest,
            }
        )

    return {
        "valid": not issues,
        "zip_path": str(ZIP_PATH),
        "checksum_path": str(SHA_PATH),
        "schema_version": SCHEMA_VERSION,
        "issues": issues,
    }


def _find_suffix(names: list[str], suffix: str) -> str | None:
    for name in names:
        if name.endswith(suffix):
            return name
    return None


if __name__ == "__main__":
    raise SystemExit(main())
