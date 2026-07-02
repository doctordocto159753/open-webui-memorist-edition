from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[2]
PINNED_OPENWEBUI_IMAGE = "ghcr.io/open-webui/open-webui:v0.9.6"


def run() -> dict[str, Any]:
    return _skipped_result("container smoke is semi-automated; run with --run-containers")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-containers", action="store_true")
    parser.add_argument("--image", default=PINNED_OPENWEBUI_IMAGE)
    parser.add_argument(
        "--compose-file",
        default=str(ROOT / "docker-compose.openwebui-smoke.yml"),
    )
    parser.add_argument("--core-url", default="http://127.0.0.1:8777/memcore/health")
    parser.add_argument("--openwebui-url", default="http://127.0.0.1:3000")
    parser.add_argument("--timeout-seconds", type=int, default=180)
    parser.add_argument("--report-out")
    args = parser.parse_args(argv)

    if not args.run_containers:
        result = _skipped_result("container run not requested")
        _write_report(args.report_out, result)
        print(json.dumps(result, indent=2))
        return 0

    result = _run_container_smoke(args)
    _write_report(args.report_out, result)
    print(json.dumps(result, indent=2))
    return 0 if result["passed"] else 1


def _run_container_smoke(args: argparse.Namespace) -> dict[str, Any]:
    compose_file = Path(args.compose_file)
    if not compose_file.exists():
        return _failed_result(f"compose file not found: {compose_file}")
    docker_check = _run(["docker", "--version"])
    if docker_check.returncode != 0:
        return _failed_result("docker is not available")
    env = {**os.environ, "OPENWEBUI_IMAGE": args.image}
    config_check = _run(
        [
            "docker",
            "compose",
            "-f",
            str(compose_file),
            "config",
        ],
        env=env,
    )
    if config_check.returncode != 0:
        return _failed_result("docker compose config failed", config_check.stderr)

    up = _run(
        [
            "docker",
            "compose",
            "-f",
            str(compose_file),
            "up",
            "-d",
            "--build",
            "memorist-core",
            "open-webui",
        ],
        env=env,
    )
    if up.returncode != 0:
        return _failed_result("docker compose up failed", up.stderr)

    core_ready = _wait_for(args.core_url, args.timeout_seconds)
    openwebui_ready = _wait_for(args.openwebui_url, args.timeout_seconds)
    return {
        "name": "openwebui_container_smoke",
        "passed": core_ready and openwebui_ready,
        "skipped": False,
        "classification": "semi-automated",
        "may_block_release": False,
        "release_gate_counted": False,
        "pinned_image": args.image,
        "compose_file": str(compose_file),
        "core_ready": core_ready,
        "openwebui_ready": openwebui_ready,
        "manual_filter_install_required": True,
        "checklist": "docs/openwebui-compatibility.md",
    }


def _wait_for(url: str, timeout_seconds: int) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            with urlopen(url, timeout=5) as response:
                if response.status < 500:
                    return True
        except URLError:
            pass
        time.sleep(2)
    return False


def _run(
    command: list[str],
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def _skipped_result(reason: str) -> dict[str, Any]:
    return {
        "name": "openwebui_container_smoke",
        "passed": False,
        "skipped": True,
        "classification": "manual-only",
        "may_block_release": False,
        "release_gate_counted": False,
        "skip_reason": reason,
        "pinned_image": PINNED_OPENWEBUI_IMAGE,
        "checklist": "docs/openwebui-compatibility.md",
    }


def _failed_result(reason: str, detail: str | None = None) -> dict[str, Any]:
    return {
        "name": "openwebui_container_smoke",
        "passed": False,
        "skipped": False,
        "classification": "semi-automated",
        "may_block_release": False,
        "release_gate_counted": False,
        "failing_step": reason,
        "detail": detail,
        "pinned_image": PINNED_OPENWEBUI_IMAGE,
        "checklist": "docs/openwebui-compatibility.md",
    }


def _write_report(path: str | None, result: dict[str, Any]) -> None:
    if not path:
        return
    report_path = Path(path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
