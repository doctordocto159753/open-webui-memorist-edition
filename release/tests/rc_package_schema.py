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

from installer.scripts.runtime_contexts import (  # noqa: E402
    verify_runtime_contexts_in_zip_names,
)
from memcore.version import SCHEMA_VERSION  # noqa: E402
from release.scan_forbidden_files import scan_path  # noqa: E402

sys.path.insert(0, str(ROOT / "installer" / "scripts"))
from assemble_rc import VERSION as RC_VERSION  # noqa: E402

ZIP_PATH = ROOT / "release" / "rc" / f"memorist-openwebui-{RC_VERSION}.zip"
SHA_PATH = ROOT / "release" / "rc" / f"memorist-openwebui-{RC_VERSION}.sha256"
# The user-facing archive is flat: Memorist.cmd and the integrity metadata
# live directly under the single extracted root directory.
VERSION_SUFFIX = f"memorist-openwebui-{RC_VERSION}/VERSION.ijson"
MANIFEST_SUFFIX = f"memorist-openwebui-{RC_VERSION}/package-manifest.ijson"


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
        # The archive must carry every Docker build context the packaged
        # compose files build from source; a missing runtime tree ships a
        # package that cannot `docker compose build` on the user's machine.
        for missing in verify_runtime_contexts_in_zip_names(names):
            issues.append({"issue_code": "missing_runtime_context", "detail": missing})
        case_insensitive_names: dict[str, str] = {}
        for name in names:
            normalized = name.casefold()
            previous = case_insensitive_names.get(normalized)
            if previous is not None and previous != name:
                issues.append(
                    {
                        "issue_code": "case_insensitive_path_collision",
                        "path": name,
                        "conflicts_with": previous,
                    }
                )
            else:
                case_insensitive_names[normalized] = name
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
