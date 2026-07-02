from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[1]

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from release.scan_source_tree import scan_path  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "path",
        nargs="?",
        default=str(ROOT),
        help="Directory or ZIP to scan. Defaults to repository root.",
    )
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--allow-active-venv",
        action="store_true",
        help="Ignore the active memorist-core/.venv when this script runs inside it.",
    )
    args = parser.parse_args(argv)

    issues = scan_path(args.path)
    scanning_repo_root = Path(args.path).resolve() == ROOT.resolve()
    if scanning_repo_root:
        issues = [
            issue
            for issue in issues
            if not (issue.path == ".git" or issue.path.startswith(".git/"))
        ]
    if args.allow_active_venv and scanning_repo_root:
        issues = [
            issue
            for issue in issues
            if not (
                issue.path == "memorist-core/.venv"
                or issue.path.startswith("memorist-core/.venv/")
            )
        ]
    if args.json:
        print(json.dumps([asdict(issue) for issue in issues], ensure_ascii=False, indent=2))
    elif issues:
        for issue in issues:
            print(f"DIRTY {issue.path}: {issue.reason}")
    else:
        print("SOURCE TREE SCAN PASSED")
    return 1 if issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
