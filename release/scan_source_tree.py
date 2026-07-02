from __future__ import annotations

import argparse
import json
import sys
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path

DIRTY_PARTS = {
    ".git",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "__pycache__",
    ".venv",
    "venv",
}

TOP_LEVEL_DIRTY_PARTS = {
    "data",
    "imports",
    "exports",
    "logs",
    "dist",
    "build",
    "node_modules",
    "htmlcov",
}

DIRTY_NAMES = {
    ".env",
    ".DS_Store",
    "Thumbs.db",
    "coverage.xml",
}

DIRTY_SUFFIXES = {
    ".pyc",
    ".pyo",
    ".sqlite",
    ".sqlite-wal",
    ".sqlite-shm",
}

ALLOWED_NAMES = {
    ".dockerignore",
    ".env.example",
    ".gitignore",
}


@dataclass(frozen=True)
class SourceScanIssue:
    path: str
    reason: str


def scan_path(path: str | Path) -> list[SourceScanIssue]:
    target = Path(path)
    if target.suffix == ".zip":
        return scan_zip(target)
    return scan_directory(target)


def scan_directory(root: str | Path) -> list[SourceScanIssue]:
    root_path = Path(root)
    issues: list[SourceScanIssue] = []
    for path in sorted(root_path.rglob("*")):
        relative_path = path.relative_to(root_path).as_posix()
        issues.extend(path_issues(relative_path))
    return _dedupe(issues)


def scan_zip(path: str | Path) -> list[SourceScanIssue]:
    issues: list[SourceScanIssue] = []
    with zipfile.ZipFile(path) as package:
        for info in package.infolist():
            issues.extend(path_issues(info.filename.replace("\\", "/").strip("/")))
    return _dedupe(issues)


def path_issues(path: str) -> list[SourceScanIssue]:
    parts = [part for part in path.split("/") if part]
    if not parts:
        return []
    name = parts[-1]
    if name in ALLOWED_NAMES:
        return []

    issues: list[SourceScanIssue] = []
    if any(part in DIRTY_PARTS for part in parts) or _has_dirty_runtime_dir(parts):
        issues.append(SourceScanIssue(path, "dirty generated/runtime path part"))
    if name in DIRTY_NAMES or (name.startswith(".env.") and name != ".env.example"):
        issues.append(SourceScanIssue(path, "dirty generated/private file name"))
    if any(suffix in DIRTY_SUFFIXES for suffix in Path(name).suffixes):
        issues.append(SourceScanIssue(path, "dirty generated/runtime file suffix"))
    return issues


def _has_dirty_runtime_dir(parts: list[str]) -> bool:
    if parts[0] in TOP_LEVEL_DIRTY_PARTS:
        return True
    dirty_paths = {
        ("memorist-core", "data"),
        ("memorist-core", "eval-runs"),
        ("memorist-core", "perf-runs"),
        ("release", "build"),
        ("release", "source"),
        ("release", "artifacts", "logs"),
    }
    return any(tuple(parts[:index]) in dirty_paths for index in range(1, len(parts) + 1))


def _dedupe(issues: list[SourceScanIssue]) -> list[SourceScanIssue]:
    seen: set[tuple[str, str]] = set()
    unique: list[SourceScanIssue] = []
    for issue in issues:
        key = (issue.path, issue.reason)
        if key not in seen:
            seen.add(key)
            unique.append(issue)
    return unique


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable issues.")
    args = parser.parse_args(argv)

    issues = scan_path(args.path)
    if args.json:
        print(json.dumps([asdict(issue) for issue in issues], ensure_ascii=False, indent=2))
    elif issues:
        for issue in issues:
            print(f"DIRTY {issue.path}: {issue.reason}")
    else:
        print("SOURCE TREE SCAN PASSED")
    return 1 if issues else 0


if __name__ == "__main__":
    sys.exit(main())
