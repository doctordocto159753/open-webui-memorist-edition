from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

DIR_NAMES_TO_REMOVE = {
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "__pycache__",
    ".venv",
    "venv",
    "data",
    "dist",
    "build",
    "node_modules",
    "htmlcov",
}

TOP_LEVEL_RUNTIME_DIRS = {
    "data",
    "imports",
    "exports",
    "logs",
    "dist",
    "build",
    "node_modules",
    "htmlcov",
}

FILE_NAMES_TO_REMOVE = {
    ".env",
    ".DS_Store",
    "Thumbs.db",
    "coverage.xml",
}

FILE_SUFFIXES_TO_REMOVE = {
    ".pyc",
    ".pyo",
    ".sqlite",
    ".sqlite-wal",
    ".sqlite-shm",
}

NEVER_REMOVE_PARTS = {
    ".git",
    "docs",
    "migrations",
    "tests",
    "fixtures",
    "license-notices",
}

NEVER_REMOVE_NAMES = {
    ".dockerignore",
    ".env.example",
    ".gitignore",
    "LICENSE",
    "README.md",
}


@dataclass(frozen=True)
class Artifact:
    path: str
    artifact_type: str
    safe_to_delete: bool
    reason: str
    recommended_action: str

    @property
    def kind(self) -> str:
        return self.artifact_type

    @property
    def removable(self) -> bool:
        return self.safe_to_delete


def collect_artifacts(root: Path = ROOT) -> list[Artifact]:
    artifacts: list[Artifact] = []
    for current_root, dir_names, file_names in os.walk(root):
        current = Path(current_root)
        relative_current = current.relative_to(root) if current != root else Path()
        current_parts = set(relative_current.parts)
        if ".git" in current_parts:
            dir_names[:] = []
            continue

        for dir_name in list(dir_names):
            path = current / dir_name
            relative = path.relative_to(root).as_posix()
            parts = set(path.relative_to(root).parts)
            protected = dir_name in NEVER_REMOVE_PARTS
            if dir_name == ".git":
                artifacts.append(Artifact(relative, "directory", False, "local VCS", "keep"))
                dir_names.remove(dir_name)
                continue
            if _is_removable_dir(path, root) and not protected:
                artifacts.append(
                    Artifact(
                        relative,
                        "directory",
                        True,
                        "generated/runtime directory",
                        "delete with clean_artifacts --apply",
                    )
                )
                dir_names.remove(dir_name)

        for file_name in file_names:
            path = current / file_name
            relative = path.relative_to(root).as_posix()
            parts = set(path.relative_to(root).parts)
            if file_name in NEVER_REMOVE_NAMES or bool(parts & NEVER_REMOVE_PARTS):
                continue
            suffixes = set(path.suffixes)
            if file_name in FILE_NAMES_TO_REMOVE:
                artifacts.append(
                    Artifact(
                        relative,
                        "file",
                        True,
                        "generated/private file",
                        "delete with clean_artifacts --apply",
                    )
                )
            elif suffixes & FILE_SUFFIXES_TO_REMOVE:
                artifacts.append(
                    Artifact(
                        relative,
                        "file",
                        True,
                        "generated/runtime file",
                        "delete with clean_artifacts --apply",
                    )
                )
    return _dedupe(artifacts)


def clean_artifacts(artifacts: list[Artifact], root: Path = ROOT) -> list[Artifact]:
    removed: list[Artifact] = []
    for artifact in artifacts:
        if not artifact.removable:
            continue
        target = root / artifact.path
        if not _is_safe_target(root, target):
            continue
        if target.is_dir():
            try:
                shutil.rmtree(target)
            except PermissionError:
                continue
            removed.append(artifact)
        elif target.exists():
            try:
                target.unlink()
            except PermissionError:
                continue
            removed.append(artifact)
    return removed


def _is_safe_target(root: Path, target: Path) -> bool:
    resolved_root = root.resolve()
    resolved_target = target.resolve()
    if resolved_target == resolved_root:
        return False
    try:
        resolved_target.relative_to(resolved_root)
    except ValueError:
        return False
    parts = set(target.relative_to(root).parts)
    if ".git" in parts:
        return False
    if target.name in {".pytest_cache", ".mypy_cache", ".ruff_cache", "__pycache__"}:
        return True
    if set(target.suffixes) & {".pyc", ".pyo"}:
        return True
    return not bool(parts & NEVER_REMOVE_PARTS)


def _is_removable_dir(path: Path, root: Path) -> bool:
    relative_parts = path.relative_to(root).parts
    name = path.name
    if name in {".pytest_cache", ".mypy_cache", ".ruff_cache", "__pycache__", ".venv", "venv"}:
        return True
    if len(relative_parts) == 1 and name in TOP_LEVEL_RUNTIME_DIRS:
        return True
    return relative_parts in {
        ("memorist-core", "data"),
        ("memorist-core", "eval-runs"),
        ("memorist-core", "perf-runs"),
        ("release", "build"),
        ("release", "source"),
        ("release", "source", "_staging"),
        ("release", "artifacts", "logs"),
    }


def _dedupe(artifacts: list[Artifact]) -> list[Artifact]:
    seen: set[str] = set()
    unique: list[Artifact] = []
    for artifact in artifacts:
        if artifact.path not in seen:
            seen.add(artifact.path)
            unique.append(artifact)
    return unique


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="Report dirty artifacts.")
    parser.add_argument("--apply", action="store_true", help="Remove safe generated artifacts.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable output.")
    parser.add_argument(
        "--keep-core-venv",
        action="store_true",
        help="Retain memorist-core/.venv for checks running inside the active uv environment.",
    )
    args = parser.parse_args(argv)
    if not args.check and not args.apply:
        args.check = True

    artifacts = collect_artifacts()
    if args.keep_core_venv:
        artifacts = [item for item in artifacts if item.path != "memorist-core/.venv"]
    removed = clean_artifacts(artifacts) if args.apply else []
    remaining = collect_artifacts() if args.apply else artifacts
    if args.keep_core_venv:
        remaining = [item for item in remaining if item.path != "memorist-core/.venv"]
    remaining_safe = [item for item in remaining if item.safe_to_delete]
    remaining_retained = [item for item in remaining if not item.safe_to_delete]
    payload = {
        "status": "dirty" if remaining_safe else "clean",
        "artifact_count": len(artifacts),
        "remaining_count": len(remaining),
        "remaining_safe_to_delete_count": len(remaining_safe),
        "remaining_retained_count": len(remaining_retained),
        "safe_to_delete_count": len([item for item in artifacts if item.safe_to_delete]),
        "retained_count": len([item for item in artifacts if not item.safe_to_delete]),
        "removed_count": len(removed),
        "artifacts": [asdict(item) for item in artifacts],
        "remaining": [asdict(item) for item in remaining],
        "removed": [asdict(item) for item in removed],
    }
    write_hygiene_reports(payload)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(
            f"{payload['status'].upper()}: {payload['remaining_count']} remaining artifacts, "
            f"{payload['safe_to_delete_count']} safe to delete, "
            f"{payload['retained_count']} retained"
        )
        if args.apply:
            print(f"REMOVED: {payload['removed_count']}")
        for artifact in remaining[:50]:
            marker = "safe_to_delete" if artifact.safe_to_delete else "retained"
            print(f"- {marker}: {artifact.path} ({artifact.reason})")
        if len(remaining) > 50:
            print(f"... {len(remaining) - 50} more")
    return 1 if remaining_safe and not args.apply else 0


def write_hygiene_reports(payload: dict[str, object]) -> None:
    artifacts_dir = ROOT / "release" / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    (artifacts_dir / "root-hygiene-audit.ijson").write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    lines = [
        "# Root Hygiene Audit",
        "",
        f"- Status: `{payload['status']}`",
        f"- Remaining artifacts: `{payload['remaining_count']}`",
        f"- Remaining safe to delete: `{payload['remaining_safe_to_delete_count']}`",
        f"- Remaining retained: `{payload['remaining_retained_count']}`",
        f"- Removed artifacts: `{payload['removed_count']}`",
        "",
        "| Path | Type | Safe To Delete | Reason | Recommended Action |",
        "| --- | --- | --- | --- | --- |",
    ]
    for item in payload["remaining"]:  # type: ignore[index]
        lines.append(
            (
                "| `{path}` | {artifact_type} | {safe_to_delete} | {reason} | "
                "{recommended_action} |"
            ).format(
                **item
            )
        )
    if not payload["remaining"]:  # type: ignore[index]
        lines.append("| _none_ | | | | |")
    (artifacts_dir / "root-hygiene-audit.md").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    sys.exit(main())
