from __future__ import annotations

import hashlib
import json
import sys
import tomllib
import zipfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
CORE_SRC = ROOT / "memorist-core" / "src"
if str(CORE_SRC) not in sys.path:
    sys.path.insert(0, str(CORE_SRC))

from memcore.version import SCHEMA_VERSION, __version__  # noqa: E402

ZIP_PATH = ROOT / "release" / "rc" / "memorist-openwebui-0.2.0-beta.1.zip"
SHA_PATH = ROOT / "release" / "rc" / "memorist-openwebui-0.2.0-beta.1.sha256"
VERSION_PATH = ROOT / "release" / "memorist-openwebui" / "VERSION.ijson"
VERSION_SUFFIX = "release/memorist-openwebui/VERSION.ijson"


def run() -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    pyproject = tomllib.loads((ROOT / "memorist-core" / "pyproject.toml").read_text())
    pyproject_version = pyproject["project"]["version"]
    expected_pep440 = __version__.replace("-beta.", "b")
    if pyproject_version != expected_pep440:
        issues.append(
            {
                "issue_code": "pyproject_version_mismatch",
                "pyproject_version": pyproject_version,
                "source_version": __version__,
                "expected_pep440": expected_pep440,
            }
        )

    version_payload = _load_json(VERSION_PATH, issues, "source_version_ijson")
    if version_payload:
        if version_payload.get("memorist_core_version") != __version__:
            issues.append({"issue_code": "version_ijson_core_version_mismatch"})
        if version_payload.get("schema_version") != SCHEMA_VERSION:
            issues.append({"issue_code": "version_ijson_schema_mismatch"})

    if not ZIP_PATH.exists():
        issues.append({"issue_code": "missing_rc_zip", "path": str(ZIP_PATH)})
    if not SHA_PATH.exists():
        issues.append({"issue_code": "missing_rc_checksum", "path": str(SHA_PATH)})
    if ZIP_PATH.exists() and SHA_PATH.exists():
        expected_digest = SHA_PATH.read_text(encoding="utf-8").split()[0]
        actual_digest = hashlib.sha256(ZIP_PATH.read_bytes()).hexdigest()
        if expected_digest != actual_digest:
            issues.append({"issue_code": "rc_checksum_mismatch"})
        with zipfile.ZipFile(ZIP_PATH) as package:
            name = next(
                (item for item in package.namelist() if item.endswith(VERSION_SUFFIX)),
                None,
            )
            if name is None:
                issues.append({"issue_code": "missing_rc_internal_version"})
            else:
                rc_version = json.loads(package.read(name).decode("utf-8"))
                if rc_version.get("memorist_core_version") != __version__:
                    issues.append({"issue_code": "rc_internal_core_version_mismatch"})
                if rc_version.get("schema_version") != SCHEMA_VERSION:
                    issues.append({"issue_code": "rc_internal_schema_mismatch"})

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    if f"Package version: `{__version__}`" not in readme:
        issues.append({"issue_code": "readme_package_version_mismatch"})
    if f"Storage schema version: `{SCHEMA_VERSION}`" not in readme:
        issues.append({"issue_code": "readme_schema_version_mismatch"})
    if "Package version: `0.1.0" in readme:
        issues.append({"issue_code": "stale_0_1_package_marked_current"})

    return {
        "name": "version_consistency",
        "passed": not issues,
        "classification": "real",
        "source_version": __version__,
        "pyproject_version": pyproject_version,
        "schema_version": SCHEMA_VERSION,
        "issues": issues,
    }


def _load_json(path: Path, issues: list[dict[str, Any]], label: str) -> dict[str, Any]:
    if not path.exists():
        issues.append({"issue_code": f"missing_{label}", "path": str(path)})
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    result = run()
    print(json.dumps(result, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
