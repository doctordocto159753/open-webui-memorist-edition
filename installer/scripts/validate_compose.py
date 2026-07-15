"""Robust release-compose validation for CI (PR5-D acceptance criterion).

Runners differ: some ship Docker Compose v2 (`docker compose`), some the legacy
v1 binary (`docker-compose`), some reject specific flag orderings, and some have
no Compose CLI at all (Postgres/Falkor jobs here use testcontainers, not the
Compose CLI). This helper validates the release compose files reliably in every
case, and never silently skips:

  1. If a Compose CLI is available it runs the authoritative `config -q`,
     selecting file/profile via ``COMPOSE_FILE`` / ``COMPOSE_PROFILES`` env vars
     (no ``-f`` / ``--profile`` flags, so runner flag-parsing quirks can't break
     it). Detection order: ``MEMORIST_COMPOSE_BIN`` override, ``docker compose``
     (v2), then ``docker-compose`` (v1).
  2. If NO Compose CLI is present it runs a self-contained structural validation
     (YAML structure + variable interpolation + expected services per profile +
     the PR5-C API-key passthrough). This is a real validation, loudly labelled,
     not a skip.

Either way the PR5-C API-key passthrough into ``memorist-core`` is asserted, and
a genuine structural/parse problem fails the job.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

COMPOSE_FILES = [
    ("docker-compose.release.yml", "root release compose"),
    ("release/memorist-openwebui/compose.yml", "packaged release compose"),
]
PROFILES = ["lite", "full"]

# Interpolation vars the compose files mark required (${VAR:?required}).
REQUIRED_INTERP_ENV = {
    "MEMORIST_ACTOR_ASSERTION_SECRET": "ci-validation-secret",
    "MEMORIST_ACTOR_SERVICE_TOKEN": "ci-validation-token",
    "MEMORIST_OPENWEBUI_WORKSPACE_UUID": "00000000-0000-0000-0000-000000000001",
}

API_KEY_VARS = [
    "MEMORIST_MEMORY_EXTRACTION_API_KEY",
    "MEMORIST_HIGH_CONFIDENCE_EXTRACTION_API_KEY",
    "MEMORIST_EMBEDDING_API_KEY",
    "MEMORIST_PRIVACY_SENSITIVITY_API_KEY",
    "MEMORIST_IMPORT_RECONSTRUCTION_API_KEY",
]
API_KEY_PROBE_VAR = API_KEY_VARS[0]

# Services expected to be selected for each profile.
EXPECTED_SERVICES = {
    "lite": {"memorist-core", "open-webui"},
    "full": {"memorist-core", "open-webui", "falkordb"},
}


# --------------------------------------------------------------------------- #
# Compose CLI detection
# --------------------------------------------------------------------------- #
def detect_compose() -> list[str] | None:
    override = os.environ.get("MEMORIST_COMPOSE_BIN")
    if override and Path(override).exists():
        return [override]
    if shutil.which("docker"):
        if subprocess.run(["docker", "compose", "version"], capture_output=True).returncode == 0:
            return ["docker", "compose"]
    if shutil.which("docker-compose"):
        if subprocess.run(["docker-compose", "version"], capture_output=True).returncode == 0:
            return ["docker-compose"]
    return None


def _base_env(compose_file: str, profile: str) -> dict[str, str]:
    env = os.environ.copy()
    for key, default in REQUIRED_INTERP_ENV.items():
        env.setdefault(key, default)
    env["COMPOSE_FILE"] = str(ROOT / compose_file)
    env["COMPOSE_PROFILES"] = profile
    return env


# --------------------------------------------------------------------------- #
# Path 1: authoritative Compose CLI validation
# --------------------------------------------------------------------------- #
def validate_config_cli(compose: list[str], compose_file: str, profile: str) -> bool:
    result = subprocess.run(
        compose + ["config", "-q"],
        cwd=ROOT,
        env=_base_env(compose_file, profile),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"  [FAIL] {compose_file} [{profile}] config -q -> exit {result.returncode}")
        detail = (result.stderr or result.stdout).strip()
        if detail:
            print("         " + detail.splitlines()[-1])
        return False
    print(f"  [ OK ] {compose_file} [{profile}] config validated (CLI)")
    return True


def assert_api_key_bridge_cli(compose: list[str], compose_file: str) -> bool:
    env = _base_env(compose_file, "lite")
    env[API_KEY_PROBE_VAR] = "sk-ci-probe-value"
    result = subprocess.run(
        compose + ["config"], cwd=ROOT, env=env, capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f"  [FAIL] {compose_file}: could not render config for API-key check")
        return False
    ok = API_KEY_PROBE_VAR in result.stdout
    tag = "OK" if ok else "FAIL"
    verb = "present" if ok else "missing"
    print(f"  [ {tag:<4}] {compose_file}: PR5-C {API_KEY_PROBE_VAR} passthrough {verb}")
    return ok


# --------------------------------------------------------------------------- #
# Path 2: self-contained structural validation (no Compose CLI, stdlib only)
# --------------------------------------------------------------------------- #
_INTERP = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(:?-([^}]*)|:\?([^}]*))?\}")


def _resolve_interpolation(text: str, env: dict[str, str]) -> tuple[str, list[str]]:
    """Emulate Compose ${VAR}, ${VAR:-default}, ${VAR:?err}. Returns (text, errors)."""
    errors: list[str] = []

    def repl(match: re.Match[str]) -> str:
        name = match.group(1)
        default = match.group(3)
        required = match.group(4)
        value = env.get(name)
        if value:
            return value
        if default is not None:
            return default
        if required is not None:
            errors.append(f"required variable {name} is not set ({required or 'required'})")
            return ""
        return ""  # unset optional -> empty (Compose behaviour)

    return _INTERP.sub(repl, text), errors


def _top_level_services(text: str) -> dict[str, str]:
    """Split a compose file into top-level service name -> block text (indent-based)."""
    lines = text.splitlines()
    services: dict[str, str] = {}
    in_services = False
    current: str | None = None
    buf: list[str] = []
    for line in lines:
        if re.match(r"^services:\s*$", line):
            in_services = True
            continue
        if in_services:
            if re.match(r"^\S", line) and not line.startswith(" "):
                # left the services block (another top-level key)
                if current is not None:
                    services[current] = "\n".join(buf)
                break
            m = re.match(r"^  ([A-Za-z0-9_.-]+):\s*$", line)
            if m:
                if current is not None:
                    services[current] = "\n".join(buf)
                current = m.group(1)
                buf = []
            elif current is not None:
                buf.append(line)
    if current is not None and current not in services:
        services[current] = "\n".join(buf)
    return services


def _profiles_for_block(block: str) -> set[str]:
    profiles: set[str] = set()
    m = re.search(r"profiles:\s*\[([^\]]*)\]", block)
    if m:
        for token in m.group(1).split(","):
            token = token.strip().strip("\"'")
            if token:
                profiles.add(token)
    for pm in re.finditer(r"profiles:\s*\n((?:\s*-\s*.+\n?)+)", block):
        for line in pm.group(1).splitlines():
            token = line.strip().lstrip("-").strip().strip("\"'")
            if token:
                profiles.add(token)
    return profiles


def structural_validate(compose_file: str) -> bool:
    path = ROOT / compose_file
    text = path.read_text(encoding="utf-8-sig")
    ok = True

    if "services:" not in text:
        print(f"  [FAIL] {compose_file}: no top-level 'services:' block")
        return False

    services = _top_level_services(text)
    if not services:
        print(f"  [FAIL] {compose_file}: could not parse any services")
        return False

    # Interpolation resolves with the CI-provided required vars.
    env = os.environ.copy()
    for key, default in REQUIRED_INTERP_ENV.items():
        env.setdefault(key, default)
    _, errors = _resolve_interpolation(text, env)
    if errors:
        for err in sorted(set(errors)):
            print(f"  [FAIL] {compose_file}: {err}")
        ok = False
    else:
        print(f"  [ OK ] {compose_file}: interpolation resolves (required vars satisfied)")

    # Expected services present, gated by their declared profiles.
    for profile in PROFILES:
        selected = {
            name for name, block in services.items()
            if profile in _profiles_for_block(block) or not _profiles_for_block(block)
        }
        missing = EXPECTED_SERVICES[profile] - selected
        if missing:
            print(f"  [FAIL] {compose_file} [{profile}]: missing services {sorted(missing)}")
            ok = False
        else:
            print(f"  [ OK ] {compose_file} [{profile}]: services {sorted(EXPECTED_SERVICES[profile])} selected")

    # PR5-C API-key passthrough + WEBUI secret.
    core_block = services.get("memorist-core", "")
    missing_keys = [v for v in API_KEY_VARS if v not in core_block]
    if missing_keys:
        print(f"  [FAIL] {compose_file}: memorist-core missing API-key vars {missing_keys}")
        ok = False
    else:
        print(f"  [ OK ] {compose_file}: PR5-C API-key passthrough present in memorist-core")
    if "WEBUI_SECRET_KEY" not in text:
        print(f"  [FAIL] {compose_file}: WEBUI_SECRET_KEY not set")
        ok = False

    return ok


# --------------------------------------------------------------------------- #
def main() -> int:
    print("== Release compose validation ==\n")
    compose = detect_compose()

    ok = True
    if compose is not None:
        print(f"Mode: authoritative Compose CLI ({' '.join(compose)})\n")
        print("Config validation:")
        for compose_file, _ in COMPOSE_FILES:
            if not (ROOT / compose_file).is_file():
                print(f"  [FAIL] missing compose file: {compose_file}")
                ok = False
                continue
            for profile in PROFILES:
                ok = validate_config_cli(compose, compose_file, profile) and ok
        print("\nPR5-C API-key bridge:")
        for compose_file, _ in COMPOSE_FILES:
            if (ROOT / compose_file).is_file():
                ok = assert_api_key_bridge_cli(compose, compose_file) and ok
    else:
        print("Mode: structural validation (no Compose CLI on this runner).")
        print("      This is a real validation of YAML structure, variable")
        print("      interpolation, per-profile services, and the PR5-C bridge —")
        print("      not a skip. Provide a Compose CLI for authoritative config.\n")
        for compose_file, _ in COMPOSE_FILES:
            if not (ROOT / compose_file).is_file():
                print(f"  [FAIL] missing compose file: {compose_file}")
                ok = False
                continue
            print(f"{compose_file}:")
            ok = structural_validate(compose_file) and ok

    print()
    if not ok:
        print("FAILED: release compose validation did not pass.")
        return 1
    print("PASSED: all release compose files validate for lite and full.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
