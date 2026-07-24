from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OPENWEBUI_BASE_IMAGE = "ghcr.io/open-webui/open-webui:v0.9.6"
sys.path.insert(0, str(ROOT / "memorist-core" / "src"))

from memcore.version import SCHEMA_VERSION, __version__  # noqa: E402


def build_manifest(output_dir: str | Path) -> dict[str, object]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    manifest = {
        "memorist_version": __version__,
        "schema_version": SCHEMA_VERSION,
        "openwebui_base": OPENWEBUI_BASE_IMAGE,
        "openwebui_integration_version": "0.2.0-beta.3",
        "supported_openwebui_versions": [OPENWEBUI_BASE_IMAGE],
        "build_time": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "git_commit": _git_commit(),
        "components": {
            "memorist_core": __version__,
            "adapter": "0.2.0-beta.3",
            "ui": "model-control-contract",
        },
    }
    (root / "version-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    checksums = _checksums(
        ROOT,
        ["docker-compose.lite.yml", "docker-compose.full.yml", ".env.example"],
    )
    (root / "checksums.sha256").write_text(
        "\n".join(f"{digest}  {path}" for path, digest in checksums.items()) + "\n",
        encoding="utf-8",
    )
    return manifest


def _checksums(root: Path, paths: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for rel in paths:
        path = root / rel
        if path.exists():
            result[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def _git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except Exception:
        return "unknown"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="release/build")
    args = parser.parse_args()
    print(json.dumps(build_manifest(args.out), indent=2))


if __name__ == "__main__":
    main()
