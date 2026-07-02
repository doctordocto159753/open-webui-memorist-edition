from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any
from urllib.request import urlopen

from release.tests.full_mode_common import (
    SkipSmoke,
    docker_daemon_status,
    failed_result,
    passed_result,
    print_result,
    sanitize,
    skipped_result,
)

NAME = "full_compose_smoke"
ROOT = Path(__file__).resolve().parents[2]


def run() -> dict[str, Any]:
    docker_ok, docker_reason = docker_daemon_status()
    if not docker_ok:
        return skipped_result(
            NAME,
            "Docker is unavailable; full compose smoke cannot run.",
            docker_reason=docker_reason,
        )
    if os.environ.get("MEMORIST_FULL_COMPOSE_SMOKE", "true").lower() == "false":
        return skipped_result(
            NAME,
            "MEMORIST_FULL_COMPOSE_SMOKE=false; compose gate explicitly skipped.",
            docker_available=True,
        )
    project_name = f"memorist-full-smoke-{os.getpid()}"
    log_dir = ROOT / "release" / "artifacts" / "logs" / NAME
    log_dir.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.setdefault("MEMORIST_PORT", "8777")
    try:
        up = _compose(
            ["up", "-d", "--build", "postgres", "falkordb", "memorist-core"],
            project_name,
            env,
            timeout=300,
        )
        if up.returncode != 0:
            _collect_logs(project_name, log_dir, env)
            return failed_result(
                NAME,
                "compose_up",
                "Inspect release/artifacts/logs/full_compose_smoke for service logs.",
                output_tail=sanitize(up.stdout[-2000:]),
                logs_path=str(log_dir),
            )
        health = _wait_for_health(env.get("MEMORIST_PORT", "8777"))
        if not health.get("ok"):
            _collect_logs(project_name, log_dir, env)
            return failed_result(
                NAME,
                "memorist_health",
                "Check memorist-core logs and PostgreSQL/FalkorDB readiness.",
                health=health,
                logs_path=str(log_dir),
            )
        return passed_result(
            NAME,
            docker_compose=True,
            health=health,
            logs_path=str(log_dir),
        )
    except SkipSmoke as skip:
        return skip.result
    except Exception as error:
        _collect_logs(project_name, log_dir, env)
        return failed_result(
            NAME,
            "exception",
            "Inspect Docker Compose logs for Full Mode startup failure.",
            error_sanitized=sanitize(str(error)),
            logs_path=str(log_dir),
        )
    finally:
        _compose(["down", "-v"], project_name, env, timeout=120)


def _compose(argv: list[str], project_name: str, env: dict[str, str], timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", "compose", "-p", project_name, "-f", "docker-compose.full.yml", *argv],
        cwd=ROOT,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
    )


def _wait_for_health(port: str, timeout_seconds: int = 180) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    url = f"http://127.0.0.1:{port}/memcore/health"
    last_error = ""
    while time.monotonic() < deadline:
        try:
            with urlopen(url, timeout=3) as response:
                payload = json.loads(response.read().decode("utf-8"))
            return {
                "ok": response.status == 200,
                "url": url,
                "payload": payload,
            }
        except Exception as error:
            last_error = sanitize(str(error))
            time.sleep(2)
    return {"ok": False, "url": url, "error_sanitized": last_error}


def _collect_logs(project_name: str, log_dir: Path, env: dict[str, str]) -> None:
    completed = _compose(["logs", "--no-color"], project_name, env, timeout=60)
    (log_dir / "docker-compose-full.log").write_text(
        completed.stdout[-20000:],
        encoding="utf-8",
    )


def main() -> int:
    return print_result(run())


if __name__ == "__main__":
    raise SystemExit(main())
