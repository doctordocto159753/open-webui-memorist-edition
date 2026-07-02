from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

FORBIDDEN_DIR_NAMES = {
    ".git",
    ".github",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "__pycache__",
    ".venv",
    "venv",
    "node_modules",
    "htmlcov",
}

FORBIDDEN_FILE_SUFFIXES = {
    ".pyc",
    ".pyo",
    ".pyd",
    ".sqlite",
    ".db",
    ".log",
}


def build_package_manifest(
    root: str | Path,
    output_path: str | Path | None = None,
) -> dict[str, Any]:
    package_root = Path(root)
    files = []
    for path in sorted(package_root.rglob("*")):
        if not path.is_file():
            continue
        relative_path = path.relative_to(package_root).as_posix()
        files.append(
            {
                "path": relative_path,
                "size": path.stat().st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "role": classify_role(relative_path),
            }
        )
    manifest: dict[str, Any] = {
        "format": "memorist-package-manifest-v1",
        "file_count": len(files),
        "files": files,
    }
    if output_path is not None:
        Path(output_path).write_text(
            json.dumps(manifest, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
    return manifest


def classify_role(path: str) -> str:
    normalized = path.replace("\\", "/")
    name = Path(normalized).name
    suffix = Path(normalized).suffix
    if name in {"LICENSE"}:
        return "license"
    if name == ".env.example":
        return "config_example"
    if suffix in {".yml", ".yaml"} or "compose" in name:
        return "compose"
    if "/migrations/" in normalized or normalized.startswith("memorist-core/migrations/"):
        return "migration"
    if "/schemas/" in normalized or suffix in {".ijson", ".ijsonl"}:
        return "schema"
    if suffix in {".md", ".txt"}:
        return "doc"
    if (
        suffix in {".sh", ".ps1"}
        or "/scripts/" in normalized
        or normalized.startswith("installer/")
    ):
        return "script"
    if normalized.startswith("open-webui-integration/"):
        return "integration"
    if "/tests/" in normalized or "fixtures/" in normalized:
        return "test_fixture"
    return "source"
