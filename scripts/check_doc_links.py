"""Verify that relative Markdown links in repository docs resolve to real files.

Used by the Public Release Readiness workflow ("Docs and Links" job) and
runnable locally:

    python3 scripts/check_doc_links.py

Checks inline links/images `[text](target)` in tracked Markdown files under
the repo root, `docs/`, and the integration/package READMEs. External URLs
(http/https/mailto) and pure anchors are skipped — CI must not depend on the
network. Anchor fragments on relative links are stripped before resolution.

Historical documents under docs/historical/ are exempt: they are preserved
as-is and may reference paths from the era they describe.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SCAN_GLOBS = [
    "*.md",
    "docs/*.md",
    "docs/reference/*.md",
    "docs/fa/*.md",
    "open-webui-integration/**/*.md",
    "memorist-core/*.md",
    "release/memorist-openwebui/*.md",
    "release/memorist-openwebui/docs/*.md",
]

EXEMPT_PARTS = {"historical", "node_modules", ".git", "rc"}

LINK_RE = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")


def iter_files() -> list[Path]:
    files: set[Path] = set()
    for pattern in SCAN_GLOBS:
        for path in ROOT.glob(pattern):
            if path.is_file() and not (set(path.parts) & EXEMPT_PARTS):
                files.add(path)
    return sorted(files)


def check_file(path: Path) -> list[str]:
    problems: list[str] = []
    text = path.read_text(encoding="utf-8-sig")
    for match in LINK_RE.finditer(text):
        target = match.group(1)
        if target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        rel = target.split("#", 1)[0]
        if not rel:
            continue
        resolved = (path.parent / rel).resolve()
        if not resolved.exists():
            problems.append(f"{path.relative_to(ROOT)}: broken link -> {target}")
    return problems


def main() -> int:
    files = iter_files()
    problems: list[str] = []
    for path in files:
        problems.extend(check_file(path))
    print(f"Checked {len(files)} Markdown files.")
    if problems:
        print(f"\nFAILED: {len(problems)} broken relative link(s):")
        for problem in problems:
            print(f"  {problem}")
        return 1
    print("PASSED: all relative Markdown links resolve.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
