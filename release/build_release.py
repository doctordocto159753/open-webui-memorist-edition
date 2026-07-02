from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def build_release() -> dict[str, str]:
    from installer.scripts.assemble_rc import assemble

    from release.scan_forbidden_files import scan_path

    result = assemble()
    issues = scan_path(result["zip"])
    if issues:
        detail = "; ".join(f"{issue.path}: {issue.reason}" for issue in issues[:10])
        raise RuntimeError(f"forbidden release package contents: {detail}")
    return result


if __name__ == "__main__":
    print(json.dumps(build_release(), indent=2))
