"""Static validation for the Memorist Windows-first installer package.

Runs on Linux CI without PowerShell. Checks that:
  * required installer entry points and the shared module exist;
  * the shared module exports the functions the entry points depend on;
  * no obvious plaintext-secret leak via Write-Host/Write-Output;
  * .env templates declare the required variables (session secrets + PR5-C
    API-key role references);
  * the release compose file exposes the PR5-C API-key passthrough;
  * the installer supports -DryRun and guards writes behind it.

Exit code is non-zero when any check fails.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PKG = ROOT / "release" / "memorist-openwebui"

REQUIRED_SCRIPTS = [
    "Install-Memorist.ps1",
    "Start-Memorist.ps1",
    "Stop-Memorist.ps1",
    "Restart-Memorist.ps1",
    "Reset-Memorist-Data.ps1",
    "Uninstall-Memorist.ps1",
    "Show-Memorist-Logs.ps1",
    "Test-Memorist-Full.ps1",
    "Memorist.cmd",
    "README-LOCAL.md",
    "scripts/MemoristCommon.psm1",
]

REQUIRED_FUNCTIONS = [
    "Test-MemoristDocker",
    "Test-MemoristPortFree",
    "Get-MemoristFreeDiskGb",
    "New-MemoristSecret",
    "Get-MemoristMaskedSecret",
    "ConvertFrom-MemoristSecureString",
    "Set-MemoristEnvValue",
    "Write-MemoristEnvFile",
    "Wait-MemoristService",
    "Open-MemoristBrowser",
    "Invoke-MemoristCompose",
    "Get-MemoristApiKeyRoles",
    "Get-MemoristInstalledMode",
    "Get-MemoristComposeOverlay",
    "Test-MemoristFullReadiness",
]

# PR5-C role -> env var names the installer must write and Compose must pass.
API_KEY_VARS = [
    "MEMORIST_MEMORY_EXTRACTION_API_KEY",
    "MEMORIST_HIGH_CONFIDENCE_EXTRACTION_API_KEY",
    "MEMORIST_EMBEDDING_API_KEY",
    "MEMORIST_PRIVACY_SENSITIVITY_API_KEY",
    "MEMORIST_IMPORT_RECONSTRUCTION_API_KEY",
]

REQUIRED_ENV_VARS = [
    "MEMORIST_ACTOR_ASSERTION_SECRET",
    "MEMORIST_ACTOR_SERVICE_TOKEN",
    "WEBUI_SECRET_KEY",
    "OPEN_WEBUI_PORT",
    "MEMORIST_PORT",
    "COMPOSE_PROJECT_NAME",
    "MEMORIST_MODE",
    "MEMORIST_RUNTIME_PROFILE",
    "MEMORIST_CANONICAL_STORE",
    "MEMORIST_POSTGRES_PASSWORD",
    "MEMORIST_POSTGRES_DSN",
    *API_KEY_VARS,
]

# Variable names that hold plaintext secrets and must never be echoed.
SECRET_VARS = ["$plain", "$secure"]


class Report:
    def __init__(self) -> None:
        self.failures: list[str] = []
        self.passes: list[str] = []

    def check(self, ok: bool, label: str, detail: str = "") -> None:
        if ok:
            self.passes.append(label)
            print(f"  [ OK ] {label}")
        else:
            msg = label if not detail else f"{label} -- {detail}"
            self.failures.append(msg)
            print(f"  [FAIL] {msg}")


def check_files(report: Report) -> None:
    print("Installer files:")
    for rel in REQUIRED_SCRIPTS:
        report.check((PKG / rel).is_file(), f"present: {rel}")


def check_functions(report: Report) -> None:
    print("Shared module functions:")
    module = (PKG / "scripts" / "MemoristCommon.psm1").read_text(encoding="utf-8-sig")
    for fn in REQUIRED_FUNCTIONS:
        found = re.search(rf"function\s+{re.escape(fn)}\b", module) is not None
        report.check(found, f"function: {fn}")


def check_no_secret_echo(report: Report) -> None:
    print("Secret redaction (no plaintext echo):")
    echo_re = re.compile(r"(Write-Host|Write-Output)\b")
    offenders: list[str] = []
    for rel in REQUIRED_SCRIPTS:
        path = PKG / rel
        if path.suffix.lower() not in {".ps1", ".psm1"}:
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
            if echo_re.search(line) and any(sv in line for sv in SECRET_VARS):
                offenders.append(f"{rel}:{lineno}: {line.strip()}")
    report.check(not offenders, "no Write-Host/Write-Output of $plain/$secure", "; ".join(offenders))

    # Any log line that references a secret var must go through the mask helper.
    mask_offenders: list[str] = []
    for rel in REQUIRED_SCRIPTS:
        path = PKG / rel
        if path.suffix.lower() not in {".ps1", ".psm1"}:
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
            if "Write-MemoristLog" in line and "$plain" in line and "Get-MemoristMaskedSecret" not in line:
                mask_offenders.append(f"{rel}:{lineno}")
    report.check(not mask_offenders, "secret log lines use Get-MemoristMaskedSecret", "; ".join(mask_offenders))


def _env_keys(path: Path) -> set[str]:
    keys: set[str] = set()
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        m = re.match(r"^\s*#?\s*([A-Z0-9_]+)=", line)
        if m:
            keys.add(m.group(1))
    return keys


def check_env_templates(report: Report) -> None:
    print("Env templates:")
    for label, path in [
        ("package .env.example", PKG / ".env.example"),
        ("root .env.example", ROOT / ".env.example"),
    ]:
        if not path.is_file():
            report.check(False, f"present: {label}")
            continue
        keys = _env_keys(path)
        missing = [v for v in REQUIRED_ENV_VARS if v not in keys]
        report.check(not missing, f"{label} declares required vars", f"missing: {missing}")


def check_compose_bridge(report: Report) -> None:
    print("Compose PR5-C bridge:")
    paths = [PKG / "compose.yml", PKG / "compose.lite.yml", PKG / "compose.full.yml"]
    for path in paths:
        report.check(path.is_file(), f"present: {path.relative_to(ROOT)}")
    text = "\n".join(path.read_text(encoding="utf-8-sig") for path in paths if path.is_file())
    for label in ["package Compose architecture"]:
        missing = [v for v in API_KEY_VARS if v not in text]
        report.check(not missing, f"{label} passes API-key roles", f"missing: {missing}")
        report.check("WEBUI_SECRET_KEY" in text, f"{label} sets WEBUI_SECRET_KEY")
    full = (PKG / "compose.full.yml").read_text(encoding="utf-8-sig")
    lite = (PKG / "compose.lite.yml").read_text(encoding="utf-8-sig")
    report.check("postgres:" in full and "falkordb:" in full, "Full Compose contains PostgreSQL and FalkorDB")
    report.check("postgres:" not in lite and "falkordb:" not in lite, "Lite Compose excludes PostgreSQL and FalkorDB")
    report.check("../../" not in text and "../..\\" not in text, "Compose contexts do not escape package")
    report.check(":latest" not in text, "Compose images do not use latest tags")
    report.check("ports:" not in full.split("  postgres:", 1)[1].split("  falkordb:", 1)[0], "PostgreSQL has no host ports")


def check_dry_run(report: Report) -> None:
    print("Dry-run + destructive guards:")
    installer = (PKG / "Install-Memorist.ps1").read_text(encoding="utf-8-sig")
    report.check("[switch]$DryRun" in installer, "Install-Memorist supports -DryRun")
    # Writes must be guarded by $DryRun (the file writer only runs in the else).
    report.check(
        "if ($DryRun)" in installer and "Write-MemoristEnvFile" in installer,
        "Install-Memorist guards .env write behind -DryRun",
    )
    report.check("[switch]$NoBrowser" in installer, "Install-Memorist supports -NoBrowser")
    report.check("Get-MemoristInstalledMode" in installer, "installer preserves authoritative installed mode")
    for rel in ["Reset-Memorist-Data.ps1", "Uninstall-Memorist.ps1"]:
        text = (PKG / rel).read_text(encoding="utf-8-sig")
        report.check("Read-Host" in text and "$Force" in text, f"{rel} warns before destructive action")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="also assert dry-run/guard checks")
    args = parser.parse_args()

    print("== Memorist installer static validation ==\n")
    report = Report()
    check_files(report)
    check_functions(report)
    check_no_secret_echo(report)
    check_env_templates(report)
    check_compose_bridge(report)
    check_dry_run(report)  # always run; --dry-run kept for workflow clarity
    _ = args

    print()
    if report.failures:
        print(f"FAILED: {len(report.failures)} check(s) failed.")
        return 1
    print(f"PASSED: {len(report.passes)} checks.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
