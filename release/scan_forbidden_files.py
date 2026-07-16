from __future__ import annotations

import argparse
import json
import re
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path

FORBIDDEN_PARTS = {
    ".git",
    ".github",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "__pycache__",
    "node_modules",
    ".venv",
    "venv",
}

TOP_LEVEL_FORBIDDEN_PARTS = {
    "data",
    "imports",
    "exports",
    "logs",
    "dist",
    "build",
    "htmlcov",
}

FORBIDDEN_SUFFIXES = {
    ".pyc",
    ".pyo",
    ".pyd",
    ".sqlite",
    ".db",
    ".sqlite-shm",
    ".sqlite-wal",
    ".log",
}

FORBIDDEN_NAMES = {
    ".env",
    "coverage.xml",
    ".DS_Store",
    "Thumbs.db",
}

DEFAULT_SECRET_PATTERNS = [
    r"OPENAI_API_KEY[ \t]*=[ \t]*\S+",
    r"ANTHROPIC_API_KEY[ \t]*=[ \t]*\S+",
    r"GOOGLE_API_KEY[ \t]*=[ \t]*\S+",
    r"\bsk-[A-Za-z0-9_-]{12,}",
    r"BEGIN " r"PRIVATE KEY",
    r"(?i)(api[_-]?key|secret|token|password)[ \t]*[:=][ \t]*['\"]?[A-Za-z0-9_\-]{16,}",
]


@dataclass(frozen=True)
class ScanIssue:
    path: str
    reason: str


def scan_path(path: str | Path, extra_secret_patterns: list[str] | None = None) -> list[ScanIssue]:
    target = Path(path)
    patterns = [re.compile(pattern) for pattern in DEFAULT_SECRET_PATTERNS]
    patterns.extend(re.compile(pattern) for pattern in extra_secret_patterns or [])
    if target.suffix == ".zip":
        return _scan_zip(target, patterns)
    return _scan_directory(target, patterns)


def _scan_zip(path: Path, patterns: list[re.Pattern[str]]) -> list[ScanIssue]:
    issues: list[ScanIssue] = []
    with zipfile.ZipFile(path) as package:
        for info in package.infolist():
            normalized = info.filename.replace("\\", "/").strip("/")
            issues.extend(_path_issues(normalized))
            if info.is_dir():
                continue
            if info.file_size > 2_000_000:
                continue
            data = package.read(info)
            issues.extend(_content_issues(normalized, data, patterns))
    return issues


def _scan_directory(path: Path, patterns: list[re.Pattern[str]]) -> list[ScanIssue]:
    issues: list[ScanIssue] = []
    for file_path in sorted(path.rglob("*")):
        relative_path = file_path.relative_to(path).as_posix()
        issues.extend(_path_issues(relative_path))
        if file_path.is_file() and file_path.stat().st_size <= 2_000_000:
            issues.extend(_content_issues(relative_path, file_path.read_bytes(), patterns))
    return issues


def _path_issues(path: str) -> list[ScanIssue]:
    normalized_parts = [part for part in path.split("/") if part]
    name = normalized_parts[-1] if normalized_parts else path
    suffixes = Path(name).suffixes
    issues: list[ScanIssue] = []
    if any(part in FORBIDDEN_PARTS for part in normalized_parts) or _has_forbidden_runtime_dir(
        normalized_parts
    ):
        issues.append(ScanIssue(path, "forbidden path part"))
    if name in FORBIDDEN_NAMES or (name.startswith(".env.") and name != ".env.example"):
        issues.append(ScanIssue(path, "forbidden file name"))
    if any(suffix in FORBIDDEN_SUFFIXES for suffix in suffixes):
        issues.append(ScanIssue(path, "forbidden file suffix"))
    return issues


def _has_forbidden_runtime_dir(parts: list[str]) -> bool:
    if parts and parts[0] in TOP_LEVEL_FORBIDDEN_PARTS:
        return True
    forbidden_paths = {
        ("memorist-core", "data"),
        ("memorist-core", "eval-runs"),
        ("memorist-core", "perf-runs"),
        ("release", "build"),
        ("release", "source"),
        ("release", "artifacts", "logs"),
    }
    return any(tuple(parts[:index]) in forbidden_paths for index in range(1, len(parts) + 1))


def _content_issues(path: str, data: bytes, patterns: list[re.Pattern[str]]) -> list[ScanIssue]:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return []
    return [
        ScanIssue(path, f"secret-like content matched {pattern.pattern}")
        for pattern in patterns
        if pattern.search(text)
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path")
    parser.add_argument(
        "--patterns-json",
        help="Optional JSON file containing extra regex patterns.",
    )
    args = parser.parse_args(argv)
    extra_patterns: list[str] = []
    if args.patterns_json:
        extra_patterns = json.loads(Path(args.patterns_json).read_text(encoding="utf-8"))
    issues = scan_path(args.path, extra_patterns)
    if issues:
        for issue in issues:
            print(f"FORBIDDEN {issue.path}: {issue.reason}")
        return 1
    print("FORBIDDEN SCAN PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
