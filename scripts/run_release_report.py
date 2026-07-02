"""Run release gate report with uv-managed memorist-core dependencies."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "memorist-core"


def main() -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT)
    uv = [sys.executable, "-m", "uv"]
    command = [*uv, "run", "python", "-m", "release.tests.report", *sys.argv[1:]]
    subprocess.run(command, cwd=CORE, env=env, check=True)


if __name__ == "__main__":
    main()
