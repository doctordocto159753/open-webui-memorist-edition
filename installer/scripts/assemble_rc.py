from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

VERSION = "0.2.0-beta.1"
OPENWEBUI_BASE_IMAGE = "ghcr.io/open-webui/open-webui:v0.9.6"
POSTGRES_IMAGE = "postgres:16.9-alpine3.22"
FALKORDB_IMAGE = (
    "falkordb/falkordb@sha256:"
    "2496643cabd67e87fd82458383400c049324daec1fe674ba0db4c5bdaca5d25f"
)
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "memorist-core" / "src"))

from release.package_manifest import build_package_manifest  # noqa: E402
from release.scan_forbidden_files import scan_path  # noqa: E402

from memcore.version import SCHEMA_VERSION, __version__  # noqa: E402

RC_ROOT = ROOT / "release" / "rc"
TARGET = RC_ROOT / f"memorist-openwebui-{VERSION}"
ZIP_PATH = RC_ROOT / f"memorist-openwebui-{VERSION}.zip"
SHA_PATH = RC_ROOT / f"memorist-openwebui-{VERSION}.sha256"

EXCLUDE_PARTS = {
    ".git",
    ".github",
    ".venv",
    "venv",
    "node_modules",
    "data",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "htmlcov",
}
EXCLUDE_NAMES = {
    ZIP_PATH.name,
    SHA_PATH.name,
    ".env",
    "coverage.xml",
    ".DS_Store",
}
EXCLUDE_SUFFIXES = {
    ".sqlite",
    ".db",
    ".wal",
    ".shm",
    ".pyc",
    ".pyo",
    ".pyd",
    ".log",
}


def assemble() -> dict[str, str]:
    if TARGET.exists():
        shutil.rmtree(TARGET)
    TARGET.mkdir(parents=True)
    _write_source_version_metadata()
    _refresh_source_installer_checksums()
    for name in ["README.md", "LICENSE"]:
        source = ROOT / name
        if source.exists():
            shutil.copy2(source, TARGET / name)
    package_target = TARGET / "release" / "memorist-openwebui"
    package_target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        ROOT / "release" / "memorist-openwebui",
        package_target,
        ignore=_ignore,
    )
    _copy_installer_runtime()
    _copy_rc_docs()
    _normalize_text_files(TARGET)
    _refresh_installer_checksums()
    manifest = build_package_manifest(TARGET)
    (TARGET / "release" / "package-manifest.ijson").write_text(
        json.dumps(manifest, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    (ROOT / "release" / "package-manifest.ijson").write_text(
        json.dumps(manifest, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    _write_checksums(TARGET / "CHECKSUMS")
    issues = scan_path(TARGET)
    if issues:
        detail = "; ".join(f"{issue.path}: {issue.reason}" for issue in issues[:10])
        raise RuntimeError(f"forbidden files in RC staging directory: {detail}")
    if ZIP_PATH.exists():
        ZIP_PATH.unlink()
    with zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED) as package:
        for path in sorted(TARGET.rglob("*")):
            if path.is_file():
                package.write(path, path.relative_to(RC_ROOT).as_posix())
    digest = hashlib.sha256(ZIP_PATH.read_bytes()).hexdigest()
    SHA_PATH.write_text(f"{digest}  {ZIP_PATH.name}\n", encoding="utf-8")
    issues = scan_path(ZIP_PATH)
    if issues:
        detail = "; ".join(f"{issue.path}: {issue.reason}" for issue in issues[:10])
        raise RuntimeError(f"forbidden files in RC zip: {detail}")
    shutil.rmtree(TARGET)
    return {"zip": str(ZIP_PATH), "sha256": str(SHA_PATH), "digest": digest}


def _write_source_version_metadata() -> None:
    payload = {
        "package": "memorist-openwebui",
        "target_label": "v0.2.0-beta.1 candidate",
        "memorist_core_version": __version__,
        "schema_version": SCHEMA_VERSION,
        "openwebui_base": OPENWEBUI_BASE_IMAGE,
        "openwebui_integration_version": "0.2.0-beta.1",
        "postgres_image": POSTGRES_IMAGE,
        "falkordb_image": FALKORDB_IMAGE,
    }
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    mirror = ROOT / "release" / "memorist-openwebui"
    mirror.mkdir(parents=True, exist_ok=True)
    (mirror / "VERSION.ijson").write_text(text, encoding="utf-8")


def _ignore(directory: str, names: list[str]) -> set[str]:
    ignored = set()
    current = Path(directory)
    for name in names:
        path = current / name
        parts = set(path.parts)
        if name == ".env.example":
            ignored.discard(name)
        elif (
            name in EXCLUDE_NAMES
            or name in EXCLUDE_PARTS
            or name.startswith(".env.")
            or ({"release", "rc"}.issubset(parts) and name.startswith("memorist-openwebui-"))
            or {"release", "build"}.issubset(parts)
            or {"release", "source"}.issubset(parts)
            or {"release", "artifacts"}.issubset(parts)
            or {"release", "artifacts", "logs"}.issubset(parts)
            or path.suffix in EXCLUDE_SUFFIXES
        ):
            ignored.add(name)
    return ignored


def _copy_rc_docs() -> None:
    for name in [
        "RELEASE_NOTES.md",
        "KNOWN_LIMITATIONS.md",
        "SECURITY.md",
        "INSTALL.md",
        "UPGRADE.md",
        "HANDOFF.md",
        "RELEASE_DECISION.md",
        "change-log-after-freeze.md",
    ]:
        source = RC_ROOT / name
        if source.exists():
            shutil.copy2(source, TARGET / name)


def _copy_installer_runtime() -> None:
    """Put every build context inside the extracted installer directory."""
    runtime = TARGET / "release" / "memorist-openwebui" / "runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    core_target = runtime / "memorist-core"
    core_target.mkdir()
    for name in ["Dockerfile", "pyproject.toml", "README.md"]:
        shutil.copy2(ROOT / "memorist-core" / name, core_target / name)
    for name in ["src", "migrations"]:
        shutil.copytree(ROOT / "memorist-core" / name, core_target / name, ignore=_ignore)
    integration_target = runtime / "open-webui-integration"
    integration_target.mkdir()
    shutil.copytree(
        ROOT / "open-webui-integration" / "memorist",
        integration_target / "memorist",
        ignore=lambda directory, names: {
            name for name in names if name in {"tests", "__pycache__", ".pytest_cache"}
        },
    )


def _refresh_installer_checksums() -> None:
    script = TARGET / "release" / "memorist-openwebui" / "scripts" / "gen_checksums.py"
    subprocess.run([sys.executable, str(script)], check=True)


def _refresh_source_installer_checksums() -> None:
    script = ROOT / "release" / "memorist-openwebui" / "scripts" / "gen_checksums.py"
    subprocess.run([sys.executable, str(script)], check=True)


def _normalize_text_files(root: Path) -> None:
    """Make packaged text byte-stable across Windows and Linux assembly."""
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        data = path.read_bytes()
        if b"\0" in data:
            continue
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            continue
        normalized = text.replace("\r\n", "\n")
        if normalized != text:
            path.write_bytes(normalized.encode("utf-8"))


def _write_checksums(output: Path) -> None:
    lines = []
    for path in sorted(TARGET.rglob("*"), key=lambda p: p.relative_to(TARGET).as_posix()):
        if path.is_file() and path.name != "CHECKSUMS":
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            lines.append(f"{digest}  {path.relative_to(TARGET).as_posix()}")
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    print(assemble())
