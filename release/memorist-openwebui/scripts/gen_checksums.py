"""Generate or verify checksums.sha256 for the Memorist local package.

The package ships a checksums.sha256 that `scripts/verify-package.sh` checks with
`sha256sum -c`. This helper keeps it in sync after installer/compose edits.

Usage:
    python gen_checksums.py            # rewrite checksums.sha256
    python gen_checksums.py --check    # verify without writing (CI); non-zero on drift
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

PKG_ROOT = Path(__file__).resolve().parents[1]
CHECKSUM_FILE = PKG_ROOT / "checksums.sha256"

EXCLUDE_DIR_NAMES = {
    "data",
    "objects",
    "imports",
    "exports",
    "logs",
    "__pycache__",
    ".pytest_cache",
}
EXCLUDE_FILE_NAMES = {"checksums.sha256", "CHECKSUMS", ".env", ".DS_Store"}
EXCLUDE_SUFFIXES = {".pyc", ".pyo", ".pyd", ".sqlite", ".log"}


def _iter_files() -> list[Path]:
    files: list[Path] = []
    # Sort by the POSIX relative path so the manifest order is identical on
    # Windows (case-insensitive Path ordering) and Linux (case-sensitive).
    for path in sorted(PKG_ROOT.rglob("*"), key=lambda p: p.relative_to(PKG_ROOT).as_posix()):
        if not path.is_file():
            continue
        rel = path.relative_to(PKG_ROOT)
        if any(part in EXCLUDE_DIR_NAMES for part in rel.parts):
            continue
        if path.name in EXCLUDE_FILE_NAMES:
            continue
        if path.name.startswith(".env.") and path.name != ".env.example":
            continue
        if path.suffix in EXCLUDE_SUFFIXES:
            continue
        files.append(path)
    return files


def _compute() -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for path in _iter_files():
        data = path.read_bytes()
        # Git stores package text with LF while Windows worktrees may expose
        # CRLF. Hash a canonical representation so the tracked manifest is
        # identical on both platforms. Binary files remain byte-exact.
        if b"\0" not in data:
            try:
                data.decode("utf-8")
            except UnicodeDecodeError:
                pass
            else:
                data = data.replace(b"\r\n", b"\n")
        digest = hashlib.sha256(data).hexdigest()
        rel = path.relative_to(PKG_ROOT).as_posix()
        rows.append((digest, rel))
    return rows


def _render(rows: list[tuple[str, str]]) -> str:
    return "".join(f"{digest}  {rel}\n" for digest, rel in rows)


def check() -> int:
    computed = _render(_compute())
    if not CHECKSUM_FILE.exists():
        print("FAIL: checksums.sha256 is missing.")
        return 1
    # The manifest is generated with LF, but a Windows worktree (or a runner
    # with core.autocrlf enabled) may present it with CRLF. Only the hashed
    # content matters, so normalize the manifest's own line endings before
    # comparing to avoid a false "out of date" on such checkouts.
    current = CHECKSUM_FILE.read_text(encoding="utf-8").replace("\r\n", "\n")
    if current != computed:
        print("FAIL: checksums.sha256 is out of date. Run gen_checksums.py to refresh.")
        current_lines = set(current.splitlines())
        computed_lines = set(computed.splitlines())
        for line in sorted(computed_lines - current_lines):
            print(f"  + {line}")
        for line in sorted(current_lines - computed_lines):
            print(f"  - {line}")
        return 1
    # Sanity: the installer entry points must be covered.
    required = ["Install-Memorist.ps1", "Memorist.cmd", "scripts/MemoristCommon.psm1"]
    for name in required:
        if name not in current:
            print(f"FAIL: {name} is not listed in checksums.sha256.")
            return 1
    print(f"OK: checksums.sha256 covers {len(computed.splitlines())} files.")
    return 0


def write() -> int:
    CHECKSUM_FILE.write_bytes(_render(_compute()).encode("utf-8"))
    print(f"Wrote {CHECKSUM_FILE} ({len(_compute())} files).")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="verify only; do not write")
    args = parser.parse_args()
    return check() if args.check else write()


if __name__ == "__main__":
    sys.exit(main())
