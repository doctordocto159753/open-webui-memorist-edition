from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ROOT / "release" / "source"
DEFAULT_OUT = SOURCE_ROOT / "open-webui-memorist-edition-source.zip"
DETERMINISTIC_DATE_TIME_BASE = datetime(2000, 1, 1)
DETERMINISTIC_DATE_TIME_SPAN_SECONDS = 20 * 365 * 24 * 60 * 60

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from release.package_manifest import build_package_manifest  # noqa: E402
from release.scan_forbidden_files import scan_path as scan_forbidden  # noqa: E402
from release.scan_source_tree import scan_path as scan_source_tree  # noqa: E402

EXCLUDE_PARTS = {
    ".git",
    ".github",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "__pycache__",
    ".venv",
    "venv",
}

TOP_LEVEL_EXCLUDE_PARTS = {
    "data",
    "imports",
    "exports",
    "logs",
    "dist",
    "build",
    "node_modules",
    "htmlcov",
}

EXCLUDE_NAMES = {
    ".env",
    ".DS_Store",
    "Thumbs.db",
    "coverage.xml",
}

EXCLUDE_SUFFIXES = {
    ".pyc",
    ".pyo",
    ".pyd",
    ".sqlite",
    ".sqlite-wal",
    ".sqlite-shm",
    ".db",
    ".log",
}


def build_source_package(out_path: str | Path = DEFAULT_OUT) -> dict[str, Any]:
    resolved_out = Path(out_path)
    if not resolved_out.is_absolute():
        resolved_out = ROOT / resolved_out
    SOURCE_ROOT.mkdir(parents=True, exist_ok=True)
    staging = SOURCE_ROOT / "_staging"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)

    for path in sorted(ROOT.iterdir()):
        if path.name == "release":
            _copy_release_tree(path, staging / "release")
        elif _should_exclude(path, ROOT):
            continue
        elif path.is_dir():
            shutil.copytree(path, staging / path.name, ignore=_ignore)
        elif path.is_file():
            shutil.copy2(path, staging / path.name)

    manifest = build_package_manifest(staging)
    manifest["format"] = "memorist-source-package-manifest-v1"
    manifest_text = json.dumps(manifest, ensure_ascii=False, separators=(",", ":"))
    (staging / "source-package-manifest.ijson").write_text(manifest_text, encoding="utf-8")

    source_issues = scan_source_tree(staging)
    forbidden_issues = scan_forbidden(staging)
    if source_issues or forbidden_issues:
        detail = "; ".join(
            [f"{item.path}: {item.reason}" for item in source_issues[:10]]
            + [f"{item.path}: {item.reason}" for item in forbidden_issues[:10]]
        )
        raise RuntimeError(f"source package staging scan failed: {detail}")

    if resolved_out.exists():
        resolved_out.unlink()
    archive_date_time = _content_derived_date_time(manifest_text.encode("utf-8"))
    with zipfile.ZipFile(resolved_out, "w", zipfile.ZIP_DEFLATED) as package:
        for file_path in sorted(staging.rglob("*")):
            if file_path.is_file():
                _write_deterministic(
                    package,
                    file_path,
                    file_path.relative_to(staging).as_posix(),
                    archive_date_time,
                )

    zip_source_issues = scan_source_tree(resolved_out)
    zip_forbidden_issues = scan_forbidden(resolved_out)
    if zip_source_issues or zip_forbidden_issues:
        detail = "; ".join(
            [f"{item.path}: {item.reason}" for item in zip_source_issues[:10]]
            + [f"{item.path}: {item.reason}" for item in zip_forbidden_issues[:10]]
        )
        raise RuntimeError(f"source package zip scan failed: {detail}")

    digest = hashlib.sha256(resolved_out.read_bytes()).hexdigest()
    sha_path = resolved_out.with_suffix(resolved_out.suffix + ".sha256")
    sha_path.write_text(f"{digest}  {resolved_out.name}\n", encoding="utf-8")
    manifest_path = SOURCE_ROOT / "source-package-manifest.ijson"
    manifest_path.write_text(manifest_text, encoding="utf-8")
    shutil.rmtree(staging)
    return {
        "zip": str(resolved_out),
        "sha256": str(sha_path),
        "manifest": str(manifest_path),
        "digest": digest,
        "file_count": manifest["file_count"],
    }


def _copy_release_tree(source: Path, target: Path) -> None:
    shutil.copytree(source, target, ignore=_ignore)


def _ignore(directory: str, names: list[str]) -> set[str]:
    current = Path(directory)
    ignored = set()
    for name in names:
        path = current / name
        if _should_exclude(path, ROOT):
            ignored.add(name)
    return ignored


def _should_exclude(path: Path, root: Path) -> bool:
    try:
        parts = set(path.relative_to(root).parts)
    except ValueError:
        parts = set(path.parts)
    name = path.name
    if name in {".env.example", ".gitignore", ".dockerignore"}:
        return False
    if name.startswith(".env.") and name != ".env.example":
        return True
    if {"release", "rc"}.issubset(parts) and name.startswith("memorist-openwebui-"):
        return True
    if {"release", "source"}.issubset(parts):
        return True
    if {"release", "artifacts"}.issubset(parts):
        return True
    if name in EXCLUDE_NAMES:
        return True
    relative_parts = tuple(path.relative_to(root).parts)
    if bool(parts & EXCLUDE_PARTS) or _has_excluded_runtime_dir(relative_parts):
        return True
    return path.suffix in EXCLUDE_SUFFIXES


def _has_excluded_runtime_dir(parts: tuple[str, ...]) -> bool:
    if parts and parts[0] in TOP_LEVEL_EXCLUDE_PARTS:
        return True
    excluded_paths = {
        ("memorist-core", "data"),
        ("memorist-core", "eval-runs"),
        ("memorist-core", "perf-runs"),
        ("release", "build"),
        ("release", "source"),
        ("release", "artifacts"),
    }
    return any(parts[:index] in excluded_paths for index in range(1, len(parts) + 1))


def _content_derived_date_time(material: bytes) -> tuple[int, int, int, int, int, int]:
    seed = int.from_bytes(hashlib.sha256(material).digest()[:8], "big")
    offset = seed % DETERMINISTIC_DATE_TIME_SPAN_SECONDS
    timestamp = DETERMINISTIC_DATE_TIME_BASE + timedelta(seconds=offset - (offset % 2))
    return (
        timestamp.year,
        timestamp.month,
        timestamp.day,
        timestamp.hour,
        timestamp.minute,
        timestamp.second,
    )


def _write_deterministic(
    package: zipfile.ZipFile,
    path: Path,
    arcname: str,
    date_time: tuple[int, int, int, int, int, int],
) -> None:
    info = zipfile.ZipInfo(arcname, date_time=date_time)
    info.create_system = 3
    info.compress_type = zipfile.ZIP_DEFLATED
    mode = 0o755 if path.suffix == ".sh" else 0o644
    info.external_attr = (0o100000 | mode) << 16
    package.writestr(info, path.read_bytes())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    args = parser.parse_args(argv)
    print(json.dumps(build_source_package(args.out), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
