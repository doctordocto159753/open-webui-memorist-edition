from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    commands = [
        [sys.executable, "scripts/clean_artifacts.py", "--apply"],
        [
            sys.executable,
            "release/source_package.py",
            "--out",
            "release/source/open-webui-memorist-edition-source.zip",
        ],
        [sys.executable, "installer/scripts/assemble_rc.py"],
        [sys.executable, "scripts/baseline_check.py"],
        [sys.executable, "scripts/clean_artifacts.py", "--apply"],
        [sys.executable, "scripts/scan_source_tree.py"],
    ]
    for command in commands:
        completed = subprocess.run(command, cwd=ROOT, text=True)
        if completed.returncode != 0:
            return completed.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
