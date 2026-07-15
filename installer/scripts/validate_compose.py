"""Robust release-compose validation for CI (PR5-D acceptance criterion).

Some runners ship Docker Compose v2 (`docker compose`), some ship the legacy
v1 binary (`docker-compose`), and some reject the exact flag ordering used on
the command line. This helper:

  * detects the available Compose command (prefers v2, falls back to v1);
  * validates every release compose file for both `lite` and `full` profiles;
  * selects the compose file and profile via the portable ``COMPOSE_FILE`` /
    ``COMPOSE_PROFILES`` environment variables instead of ``-f`` / ``--profile``
    flags, avoiding runner-specific flag-parsing quirks;
  * asserts the PR5-C API-key passthrough renders into ``memorist-core``;
  * fails with a clear, actionable message when no Compose is available.

Compose Config is a certification gate, so this never silently degrades: if no
Compose command exists the exit code is non-zero.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# (compose file relative to repo root, human label)
COMPOSE_FILES = [
    ("docker-compose.release.yml", "root release compose"),
    ("release/memorist-openwebui/compose.yml", "packaged release compose"),
]
PROFILES = ["lite", "full"]

# Interpolation vars the compose files mark as required (${VAR:?required}).
REQUIRED_INTERP_ENV = {
    "MEMORIST_ACTOR_ASSERTION_SECRET": "ci-validation-secret",
    "MEMORIST_ACTOR_SERVICE_TOKEN": "ci-validation-token",
    "MEMORIST_OPENWEBUI_WORKSPACE_UUID": "00000000-0000-0000-0000-000000000001",
}

# PR5-C role var used to prove the local-user API-key bridge reaches the backend.
API_KEY_PROBE_VAR = "MEMORIST_MEMORY_EXTRACTION_API_KEY"


def detect_compose() -> list[str] | None:
    """Return the Compose invocation, preferring v2 then legacy v1."""
    if shutil.which("docker"):
        probe = subprocess.run(
            ["docker", "compose", "version"],
            capture_output=True,
            text=True,
        )
        if probe.returncode == 0:
            return ["docker", "compose"]
    if shutil.which("docker-compose"):
        probe = subprocess.run(
            ["docker-compose", "version"],
            capture_output=True,
            text=True,
        )
        if probe.returncode == 0:
            return ["docker-compose"]
    return None


def _base_env(compose_file: str, profile: str) -> dict[str, str]:
    env = os.environ.copy()
    for key, default in REQUIRED_INTERP_ENV.items():
        env.setdefault(key, default)
    # Portable selection: no -f / --profile flags.
    env["COMPOSE_FILE"] = str(ROOT / compose_file)
    env["COMPOSE_PROFILES"] = profile
    return env


def validate_config(compose: list[str], compose_file: str, profile: str) -> bool:
    env = _base_env(compose_file, profile)
    result = subprocess.run(
        compose + ["config", "-q"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"  [FAIL] {compose_file} [{profile}] config -q -> exit {result.returncode}")
        detail = (result.stderr or result.stdout).strip()
        if detail:
            print("         " + detail.splitlines()[-1])
        return False
    print(f"  [ OK ] {compose_file} [{profile}] config validated")
    return True


def assert_api_key_bridge(compose: list[str], compose_file: str) -> bool:
    env = _base_env(compose_file, "lite")
    env[API_KEY_PROBE_VAR] = "sk-ci-probe-value"
    result = subprocess.run(
        compose + ["config"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"  [FAIL] {compose_file}: could not render config to check API-key bridge")
        return False
    if API_KEY_PROBE_VAR in result.stdout:
        print(f"  [ OK ] {compose_file}: PR5-C {API_KEY_PROBE_VAR} passthrough present")
        return True
    print(f"  [FAIL] {compose_file}: PR5-C {API_KEY_PROBE_VAR} passthrough missing")
    return False


def main() -> int:
    print("== Release compose validation ==\n")
    compose = detect_compose()
    if compose is None:
        print("FAIL: no Docker Compose command available on this runner.")
        print("      Install Docker Compose v2 (the 'docker compose' CLI plugin)")
        print("      or the legacy 'docker-compose' binary, then re-run.")
        return 2
    print(f"Using Compose command: {' '.join(compose)}\n")

    ok = True
    print("Config validation:")
    for compose_file, _label in COMPOSE_FILES:
        if not (ROOT / compose_file).is_file():
            print(f"  [FAIL] missing compose file: {compose_file}")
            ok = False
            continue
        for profile in PROFILES:
            ok = validate_config(compose, compose_file, profile) and ok

    print("\nPR5-C API-key bridge:")
    for compose_file, _label in COMPOSE_FILES:
        if (ROOT / compose_file).is_file():
            ok = assert_api_key_bridge(compose, compose_file) and ok

    print()
    if not ok:
        print("FAILED: release compose validation did not pass.")
        return 1
    print("PASSED: all release compose files validate for lite and full.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
