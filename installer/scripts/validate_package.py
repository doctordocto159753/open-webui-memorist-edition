"""Validate the final user-facing ZIP against its own integrity claims.

Run after ``assemble_rc.py``:

    python installer/scripts/validate_package.py release/rc/memorist-openwebui-<version>.zip

The validator extracts the archive into a fresh directory whose path contains
spaces (mirroring a realistic Windows Desktop extraction), then verifies:

* the required extracted-root layout (one-click contract);
* ``package-manifest.ijson`` truthfully describes the final contents: every
  declared file exists with the declared size and SHA-256, and no file ships
  undeclared except the manifest itself (the single documented exclusion);
* ``checksums.sha256`` verifies against the extracted files using the same
  newline normalization the generator applies, has no stale entries, and
  covers the installer entry points;
* no forbidden or source-tree-only paths leaked into the archive.

Exit code 0 only when every check passes.
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from installer.scripts.runtime_contexts import verify_runtime_contexts  # noqa: E402
from release.scan_forbidden_files import scan_path  # noqa: E402

REQUIRED_ROOT_ENTRIES = [
    "Memorist.cmd",
    "Install-Memorist.ps1",
    "Start-Memorist.ps1",
    "Stop-Memorist.ps1",
    "Restart-Memorist.ps1",
    "Show-Memorist-Logs.ps1",
    "Reset-Memorist-Data.ps1",
    "Uninstall-Memorist.ps1",
    "Test-Memorist-Full.ps1",
    "compose.yml",
    "compose.lite.yml",
    "compose.full.yml",
    "runtime",
    "docs",
    "checksums.sha256",
    "package-manifest.ijson",
]

REQUIRED_RUNTIME_ENTRIES = [
    "runtime/memorist-core/Dockerfile",
    "runtime/open-webui-integration/memorist/backend/openwebui_entrypoint.py",
    "runtime/open-webui-integration/memorist/backend/route_order.py",
    "runtime/open-webui-integration/memorist/backend/filter_provisioning.py",
    # The two build inputs whose absence produced the reported
    # "COPY ... not found" Docker failures. They are COPY'd by
    # runtime/openwebui-image/Dockerfile and must ship in the archive.
    "runtime/open-webui-integration/memorist/backend/patch_openwebui_backend.py",
    "runtime/open-webui-integration/memorist/ui/surfaces.ts",
    "runtime/openwebui-image/Dockerfile",
    "runtime/openwebui-image/source-pin.json",
    "runtime/openwebui-image/prepare_frontend_tree.sh",
    "runtime/openwebui-image/patches/0001-admin-settings-memorist-navigation.patch",
    "runtime/openwebui-image/patches/0002-composer-memorist-toggle.patch",
    "runtime/openwebui-image/patches/0003-response-message-memory-disclosure.patch",
]

FORBIDDEN_TREE_PARTS = {".git", ".github", "node_modules", "__pycache__", ".venv", "rc"}

CHECKSUM_EXCLUDED_DIRS = {
    "data",
    "objects",
    "imports",
    "exports",
    "logs",
    "__pycache__",
    ".pytest_cache",
}
CHECKSUM_EXCLUDED_NAMES = {"checksums.sha256", "CHECKSUMS", "package-manifest.ijson", ".env", ".DS_Store"}
CHECKSUM_EXCLUDED_SUFFIXES = {".pyc", ".pyo", ".pyd", ".sqlite", ".log"}


class ValidationFailure(Exception):
    pass


def _fail(message: str) -> None:
    raise ValidationFailure(message)


def _normalized_digest(path: Path) -> str:
    data = path.read_bytes()
    if b"\0" not in data:
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            pass
        else:
            data = data.replace(b"\r\n", b"\n")
    return hashlib.sha256(data).hexdigest()


def _raw_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate(zip_path: Path) -> dict[str, object]:
    if not zip_path.is_file():
        _fail(f"archive not found: {zip_path}")
    with tempfile.TemporaryDirectory(prefix="memorist validate ") as raw_tmp:
        # A path containing spaces, deliberately.
        extract_root = Path(raw_tmp) / "Memorist Final Test"
        extract_root.mkdir()
        with zipfile.ZipFile(zip_path) as archive:
            archive.extractall(extract_root)
        top = [entry for entry in extract_root.iterdir()]
        if len(top) != 1 or not top[0].is_dir():
            _fail(f"archive must contain exactly one root directory, found {[t.name for t in top]}")
        package = top[0]

        for entry in REQUIRED_ROOT_ENTRIES:
            if not (package / entry).exists():
                _fail(f"required root entry missing: {entry}")
        for entry in REQUIRED_RUNTIME_ENTRIES:
            if not (package / entry).is_file():
                _fail(f"required runtime file missing: {entry}")

        # Drift guard: assert every COPY source in the shipped Dockerfiles
        # resolves inside its build context, so the extracted package can
        # actually run `docker compose build` without a "COPY ... not found".
        runtime_issues = verify_runtime_contexts(package)
        if runtime_issues:
            _fail("incomplete runtime build contexts: " + "; ".join(runtime_issues[:10]))

        actual_files = {
            path.relative_to(package).as_posix(): path
            for path in sorted(package.rglob("*"))
            if path.is_file()
        }
        for relative in actual_files:
            parts = set(Path(relative).parts[:-1])
            leaked = parts & FORBIDDEN_TREE_PARTS
            if leaked:
                _fail(f"source-tree-only path shipped in archive: {relative}")

        manifest = json.loads((package / "package-manifest.ijson").read_text(encoding="utf-8"))
        declared = {item["path"]: item for item in manifest["files"]}
        if manifest.get("file_count") != len(declared):
            _fail(
                "manifest file_count is untruthful: "
                f"{manifest.get('file_count')} declared vs {len(declared)} listed"
            )
        undeclared = set(actual_files) - set(declared) - {"package-manifest.ijson"}
        if undeclared:
            _fail(f"files shipped but not declared in the manifest: {sorted(undeclared)[:10]}")
        missing = set(declared) - set(actual_files)
        if missing:
            _fail(f"files declared in the manifest but absent: {sorted(missing)[:10]}")
        for relative, item in declared.items():
            path = actual_files[relative]
            if path.stat().st_size != item["size"]:
                _fail(f"manifest size mismatch for {relative}")
            if _raw_digest(path) != item["sha256"]:
                _fail(f"manifest sha256 mismatch for {relative}")

        checksum_lines = (
            (package / "checksums.sha256").read_text(encoding="utf-8").replace("\r\n", "\n")
        )
        listed: dict[str, str] = {}
        for line in checksum_lines.splitlines():
            if not line.strip():
                continue
            digest, _, relative = line.partition("  ")
            listed[relative.strip()] = digest.strip()
        for required in ("Install-Memorist.ps1", "Memorist.cmd", "scripts/MemoristCommon.psm1"):
            if required not in listed:
                _fail(f"checksums.sha256 does not cover entry point {required}")
        for relative, digest in listed.items():
            path = package / relative
            if not path.is_file():
                _fail(f"stale checksums.sha256 entry (file absent): {relative}")
            if _normalized_digest(path) != digest:
                _fail(f"checksums.sha256 digest mismatch for {relative}")
        expected_checksum_files = {
            relative
            for relative, path in actual_files.items()
            if not (
                set(Path(relative).parts[:-1]) & CHECKSUM_EXCLUDED_DIRS
                or Path(relative).name in CHECKSUM_EXCLUDED_NAMES
                or (Path(relative).name.startswith(".env.") and Path(relative).name != ".env.example")
                or Path(relative).suffix in CHECKSUM_EXCLUDED_SUFFIXES
            )
        }
        uncovered = expected_checksum_files - set(listed)
        if uncovered:
            _fail(f"files not covered by checksums.sha256: {sorted(uncovered)[:10]}")

        issues = scan_path(package)
        if issues:
            detail = "; ".join(f"{issue.path}: {issue.reason}" for issue in issues[:10])
            _fail(f"forbidden content in archive: {detail}")

        return {
            "archive": zip_path.name,
            "sha256": _raw_digest(zip_path),
            "file_count": len(actual_files),
            "declared_count": len(declared),
            "package_root": package.name,
        }


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    try:
        summary = validate(Path(sys.argv[1]))
    except ValidationFailure as failure:
        print(f"FAIL: {failure}")
        return 1
    print("OK: " + json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
