"""Authoritative Lite/Full release Compose validation.

This check deliberately requires a working Compose CLI. Static YAML checks are
covered by validate_installer.py; release claims must use Compose's own merged
configuration and may not turn a missing Docker/Compose installation green.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "release" / "memorist-openwebui"
BASE = PACKAGE / "compose.yml"
OVERLAYS = {mode: PACKAGE / f"compose.{mode}.yml" for mode in ("lite", "full")}
EXPECTED = {
    "lite": {"memorist-core", "open-webui"},
    "full": {"memorist-core", "open-webui", "postgres", "falkordb"},
}
FULL_ENV = {
    "MEMORIST_MODE": "full",
    "MEMORIST_RUNTIME_PROFILE": "full",
    "MEMORIST_CANONICAL_STORE": "postgres",
    "MEMORIST_GRAPH_BACKEND": "falkordb",
    "MEMORIST_HOT_SCHEDULER": "in_memory",
    "MEMORIST_ENABLE_OPENWEBUI_ADAPTER": "true",
    "MEMORIST_ENABLE_GRAPH_PROJECTION": "true",
    "MEMORIST_ENABLE_MEMORY_WORKER": "true",
    "MEMORIST_ENABLE_IMPORT_SYSTEM": "true",
    "MEMORIST_ENABLE_MEMORY_CONTEXT_ATTACHMENT": "true",
    "MEMORIST_ENABLE_ACTIVE_MEMORY_BLOCKS": "true",
    "MEMORIST_ENABLE_USER_PROFILE_PROJECTION": "true",
    "MEMORIST_ENABLE_FORGETTING_PIPELINE": "true",
}
API_KEYS = {
    "MEMORIST_MEMORY_EXTRACTION_API_KEY",
    "MEMORIST_HIGH_CONFIDENCE_EXTRACTION_API_KEY",
    "MEMORIST_EMBEDDING_API_KEY",
    "MEMORIST_PRIVACY_SENSITIVITY_API_KEY",
    "MEMORIST_IMPORT_RECONSTRUCTION_API_KEY",
}


def detect_compose() -> list[str] | None:
    override = os.environ.get("MEMORIST_COMPOSE_BIN")
    if override and Path(override).is_file():
        return [override]
    if shutil.which("docker") and subprocess.run(
        ["docker", "compose", "version"], capture_output=True
    ).returncode == 0:
        return ["docker", "compose"]
    if shutil.which("docker-compose"):
        return ["docker-compose"]
    return None


def validation_env(mode: str) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "COMPOSE_PROJECT_NAME": "memorist-validation",
            "WEBUI_SECRET_KEY": "ci-webui-secret",
            "MEMORIST_ACTOR_ASSERTION_SECRET": "ci-actor-secret",
            "MEMORIST_ACTOR_SERVICE_TOKEN": "ci-service-token",
            "MEMORIST_OPENWEBUI_WORKSPACE_UUID": "00000000-0000-0000-0000-000000000001",
        }
    )
    if mode == "full":
        env["MEMORIST_POSTGRES_PASSWORD"] = "ci-postgres-password"
        env["MEMORIST_POSTGRES_DSN"] = (
            "postgresql://memorist:ci-postgres-password@postgres:5432/memorist"
        )
    return env


def render(compose: list[str], mode: str) -> dict[str, object]:
    command = compose + [
        "-f", str(BASE), "-f", str(OVERLAYS[mode]), "config", "--format", "json"
    ]
    result = subprocess.run(
        command, cwd=PACKAGE, env=validation_env(mode), capture_output=True, text=True
    )
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout).strip())
    return json.loads(result.stdout)


def validate_mode(compose: list[str], mode: str) -> list[str]:
    failures: list[str] = []
    config = render(compose, mode)
    services = config.get("services", {})
    actual = set(services)
    if actual != EXPECTED[mode]:
        failures.append(f"{mode}: services {sorted(actual)} != {sorted(EXPECTED[mode])}")
    core = services.get("memorist-core", {})
    core_env = core.get("environment", {})
    if mode == "full":
        for key, expected in FULL_ENV.items():
            if str(core_env.get(key, "")).lower() != expected:
                failures.append(f"full: {key} is not {expected}")
        if "MEMORIST_POSTGRES_DSN" not in core_env:
            failures.append("full: PostgreSQL DSN missing")
        for service in ("postgres", "falkordb"):
            if services.get(service, {}).get("ports"):
                failures.append(f"full: {service} must not publish a host port")
    else:
        if core_env.get("MEMORIST_CANONICAL_STORE") != "sqlite":
            failures.append("lite: canonical store is not SQLite")
        if {"postgres", "falkordb"} & actual:
            failures.append("lite: database/graph service unexpectedly selected")
    missing_keys = API_KEYS - set(core_env)
    if missing_keys:
        failures.append(f"{mode}: API-key passthrough missing {sorted(missing_keys)}")

    package_root = PACKAGE.resolve()
    build_context = Path(core.get("build", {}).get("context", "")).resolve()
    try:
        build_context.relative_to(package_root)
    except ValueError:
        failures.append(f"{mode}: build context escapes package: {build_context}")
    print(f"  [{'OK' if not failures else 'FAIL'}] {mode}: {sorted(actual)}")
    return failures


def main() -> int:
    missing = [str(path) for path in (BASE, *OVERLAYS.values()) if not path.is_file()]
    if missing:
        print(f"FAIL: missing Compose files: {missing}")
        return 1
    compose = detect_compose()
    if compose is None:
        print("FAIL: Docker Compose is required for authoritative release validation.")
        return 1
    failures: list[str] = []
    for mode in ("lite", "full"):
        try:
            failures.extend(validate_mode(compose, mode))
        except Exception as error:
            failures.append(f"{mode}: {error}")
    for failure in failures:
        print(f"  [FAIL] {failure}")
    if failures:
        return 1
    print("PASSED: authoritative Lite and Full Compose configurations are coherent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
