from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

VERSION = "0.2.0-beta.2"
OPENWEBUI_BASE_IMAGE = (
    "ghcr.io/open-webui/open-webui:v0.9.6@sha256:"
    "90eae5b419e40b4c3dd684582b2c83440b36f9ae2f6532c09639b2ba4ee65158"
)
MEMORIST_OPENWEBUI_IMAGE = f"memorist/openwebui:{VERSION}"
POSTGRES_IMAGE = "postgres:16.9-alpine3.22"
FALKORDB_IMAGE = (
    "falkordb/falkordb@sha256:"
    "2496643cabd67e87fd82458383400c049324daec1fe674ba0db4c5bdaca5d25f"
)
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "memorist-core" / "src"))

from installer.scripts.runtime_contexts import (  # noqa: E402
    REQUIRED_RUNTIME_PATHS,
    verify_runtime_contexts,
)
from release.package_manifest import build_package_manifest  # noqa: E402
from release.scan_forbidden_files import scan_path  # noqa: E402

from memcore.version import SCHEMA_VERSION, __version__  # noqa: E402

# Fixed archive-entry timestamp so the ZIP is byte-reproducible across clean
# checkouts. Git does not preserve file mtimes, so without this the embedded
# per-entry timestamps (and therefore the archive digest) would vary run to run
# even though the packaged contents are identical. 1980-01-01 is the ZIP epoch.
DETERMINISTIC_DATE_TIME = (1980, 1, 1, 0, 0, 0)

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

    # The installer package itself is the archive root. A user extracting the
    # ZIP must see Memorist.cmd immediately, not under release/memorist-openwebui.
    shutil.copytree(
        ROOT / "release" / "memorist-openwebui",
        TARGET,
        dirs_exist_ok=True,
        ignore=_ignore,
    )
    _copy_installer_runtime()
    _copy_archive_docs()
    _normalize_text_files(TARGET)

    # Fail before publishing: the archive is worthless if the Docker build
    # contexts it ships are incomplete, so refuse to produce a manifest or ZIP
    # for a package whose runtime trees regressed. This turns a
    # "COPY ... not found" failure on the end user's machine into a build-time
    # error here, at assembly time.
    runtime_issues = verify_runtime_contexts(TARGET)
    if runtime_issues:
        detail = "; ".join(runtime_issues[:10])
        raise RuntimeError(f"incomplete runtime build contexts in RC staging: {detail}")

    # Integrity layering (documented in release/packaging.md):
    #   1. checksums.sha256 covers every shipped file except itself and
    #      package-manifest.ijson (integrity metadata is excluded from the
    #      inner layer so no self-hashing cycle exists);
    #   2. package-manifest.ijson is generated LAST over the final tree and
    #      covers everything including checksums.sha256, excluding only
    #      itself. Nothing may be written into TARGET after this point.
    #   3. The ZIP's SHA-256 (sidecar .sha256) covers the whole archive.
    _refresh_installer_checksums()
    manifest = build_package_manifest(TARGET, exclude_names={"package-manifest.ijson"})
    _assert_manifest_covers_runtime(manifest)
    (TARGET / "package-manifest.ijson").write_text(
        json.dumps(manifest, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    (ROOT / "release" / "package-manifest.ijson").write_text(
        json.dumps(manifest, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )

    issues = scan_path(TARGET)
    if issues:
        detail = "; ".join(f"{issue.path}: {issue.reason}" for issue in issues[:10])
        raise RuntimeError(f"forbidden files in RC staging directory: {detail}")

    if ZIP_PATH.exists():
        ZIP_PATH.unlink()
    with zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED) as package:
        for path in sorted(TARGET.rglob("*"), key=lambda p: p.relative_to(TARGET).as_posix()):
            if path.is_file():
                _write_deterministic(package, path, path.relative_to(RC_ROOT).as_posix())

    digest = hashlib.sha256(ZIP_PATH.read_bytes()).hexdigest()
    SHA_PATH.write_text(f"{digest}  {ZIP_PATH.name}\n", encoding="utf-8")
    issues = scan_path(ZIP_PATH)
    if issues:
        detail = "; ".join(f"{issue.path}: {issue.reason}" for issue in issues[:10])
        raise RuntimeError(f"forbidden files in RC zip: {detail}")

    shutil.rmtree(TARGET)
    return {"zip": str(ZIP_PATH), "sha256": str(SHA_PATH), "digest": digest}


def _write_deterministic(package: zipfile.ZipFile, path: Path, arcname: str) -> None:
    """Add ``path`` to the archive with a fixed timestamp for reproducibility.

    Only the entry's stored mtime is normalized; file content and Unix mode
    (git preserves the executable bit) are carried through unchanged, so the
    integrity layers that hash file bytes are unaffected.
    """
    info = zipfile.ZipInfo(arcname, date_time=DETERMINISTIC_DATE_TIME)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = (path.stat().st_mode & 0xFFFF) << 16
    package.writestr(info, path.read_bytes())


def _write_source_version_metadata() -> None:
    payload = {
        "package": "memorist-openwebui",
        "target_label": f"v{VERSION} candidate",
        "memorist_core_version": __version__,
        "schema_version": SCHEMA_VERSION,
        "openwebui_base": OPENWEBUI_BASE_IMAGE,
        "memorist_openwebui_image": MEMORIST_OPENWEBUI_IMAGE,
        "openwebui_integration_version": VERSION,
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


def _copy_archive_docs() -> None:
    """Ship canonical current docs; never mix frozen RC handoff narratives in."""
    for source, relative in [
        (ROOT / "LICENSE", Path("LICENSE")),
        (ROOT / "SECURITY.md", Path("SECURITY.md")),
        (ROOT / "RELEASE_NOTES.md", Path("RELEASE_NOTES.md")),
        (ROOT / "docs" / "INSTALLATION.md", Path("docs/INSTALLATION.md")),
        # The installer skeleton already contains docs/troubleshooting.md.
        # Replace it with the canonical document using the same casing so the
        # ZIP is byte-for-byte portable to Windows' case-insensitive filesystem.
        (ROOT / "docs" / "TROUBLESHOOTING.md", Path("docs/troubleshooting.md")),
        (ROOT / "docs" / "ARCHITECTURE.md", Path("docs/ARCHITECTURE.md")),
        (ROOT / "docs" / "MEMORY_MACHINE.md", Path("docs/MEMORY_MACHINE.md")),
        (ROOT / "docs" / "DEVELOPMENT.md", Path("docs/DEVELOPMENT.md")),
        (
            ROOT / "docs" / "reference" / "backup-restore.md",
            Path("docs/reference/backup-restore.md"),
        ),
    ]:
        if not source.is_file():
            continue
        destination = TARGET / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def _assert_manifest_covers_runtime(manifest: dict[str, object]) -> None:
    """Every required runtime path must be represented in the shipped manifest.

    A required file must be declared verbatim; a required directory must have at
    least one file declared beneath it. This keeps the manifest an honest
    inventory of the build contexts even if a future exclusion rule is added.
    """
    declared = {item["path"] for item in manifest["files"]}  # type: ignore[index]
    missing: list[str] = []
    for rel in REQUIRED_RUNTIME_PATHS:
        target = (TARGET / rel)
        if target.is_dir():
            prefix = f"{rel}/"
            if not any(path.startswith(prefix) for path in declared):
                missing.append(rel)
        elif rel not in declared:
            missing.append(rel)
    if missing:
        detail = "; ".join(sorted(missing)[:10])
        raise RuntimeError(f"package manifest omits required runtime paths: {detail}")


def _copy_installer_runtime() -> None:
    """Put every build context inside the extracted installer directory."""
    runtime = TARGET / "runtime"
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
    # The derivative Open WebUI image build context (Dockerfile, pinned source
    # manifest, frontend overlay, patch layer). Compose builds it with
    # context ./runtime so the package stays self-contained.
    shutil.copytree(
        ROOT / "release" / "openwebui-image",
        runtime / "openwebui-image",
        ignore=_ignore,
    )


def _refresh_installer_checksums() -> None:
    script = TARGET / "scripts" / "gen_checksums.py"
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


if __name__ == "__main__":
    print(assemble())
