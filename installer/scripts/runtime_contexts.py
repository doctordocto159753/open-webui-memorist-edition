"""Authoritative contract for the runtime build contexts shipped in the package.

The user-facing package builds two images entirely from source that travels
inside the archive (see ``compose.yml`` + ``compose.full.yml``):

* ``open-webui`` — built with context ``./runtime`` and dockerfile
  ``openwebui-image/Dockerfile`` (the derivative Open WebUI image);
* ``memorist-core`` — built with context ``./runtime/memorist-core``.

If any file those Dockerfiles ``COPY`` is absent from the archive, the build
fails at ``docker compose build`` with an unrecoverable
``COPY ... not found`` error on the user's machine — after they have already
extracted and launched the installer. That failure must be impossible to ship:
this module is the single source of truth every packaging gate consults, so a
regression in the assembler (a dropped tree, a tightened ignore rule) is caught
before the archive is written, not by an end user.

Two independent checks are provided and both must pass:

1. ``REQUIRED_RUNTIME_PATHS`` — an explicit allow-list of directories and files
   that must exist (directories must be non-empty). This gives precise,
   human-readable errors naming exactly what is missing.
2. ``iter_copy_sources`` / the Dockerfile scan — parses every ``COPY``
   instruction in the shipped Dockerfiles and asserts each build-context source
   resolves inside the package. This is drift-proof: adding a new ``COPY`` to a
   Dockerfile without shipping its source, or removing a shipped tree a
   Dockerfile still needs, fails the gate automatically.
"""

from __future__ import annotations

import json
import shlex
from dataclasses import dataclass
from pathlib import Path

# Build contexts the packaged compose files build from source, expressed
# relative to the extracted package root. ``dockerfile`` is relative to the
# package root as well (Docker resolves it independently of the context path).
@dataclass(frozen=True)
class BuildContext:
    service: str
    context: str
    dockerfile: str


RUNTIME_BUILD_CONTEXTS: tuple[BuildContext, ...] = (
    BuildContext(
        service="open-webui",
        context="runtime",
        dockerfile="runtime/openwebui-image/Dockerfile",
    ),
    BuildContext(
        service="memorist-core",
        context="runtime/memorist-core",
        dockerfile="runtime/memorist-core/Dockerfile",
    ),
)

# Directories that must be present AND non-empty in the extracted package.
REQUIRED_RUNTIME_DIRS: tuple[str, ...] = (
    "runtime/memorist-core",
    "runtime/memorist-core/src",
    "runtime/memorist-core/migrations",
    "runtime/openwebui-image",
    "runtime/openwebui-image/patches",
    "runtime/openwebui-image/frontend-overlay",
    "runtime/open-webui-integration/memorist",
    "runtime/open-webui-integration/memorist/backend",
    # The frontend build copies every Memorist UI TypeScript module out of this
    # directory; its absence is the reported "COPY .../ui/ not found" failure.
    "runtime/open-webui-integration/memorist/ui",
    "runtime/open-webui-integration/memorist/filter",
    "runtime/open-webui-integration/memorist/shared",
)

# Individual files that must be present in the extracted package. These are the
# concrete inputs the Dockerfiles and the frontend-prep script consume; missing
# any of them breaks the build.
REQUIRED_RUNTIME_FILES: tuple[str, ...] = (
    "runtime/memorist-core/Dockerfile",
    "runtime/memorist-core/pyproject.toml",
    "runtime/openwebui-image/Dockerfile",
    "runtime/openwebui-image/source-pin.json",
    "runtime/openwebui-image/prepare_frontend_tree.sh",
    "runtime/openwebui-image/patches/0001-admin-settings-memorist-navigation.patch",
    "runtime/openwebui-image/patches/0002-composer-memorist-toggle.patch",
    "runtime/openwebui-image/patches/0003-response-message-memory-disclosure.patch",
    # The backend patch that guards Open WebUI's message consolidation; the
    # second reported "COPY ... patch_openwebui_backend.py not found" failure.
    "runtime/open-webui-integration/memorist/backend/patch_openwebui_backend.py",
    "runtime/open-webui-integration/memorist/backend/openwebui_entrypoint.py",
    "runtime/open-webui-integration/memorist/backend/route_order.py",
    "runtime/open-webui-integration/memorist/backend/filter_provisioning.py",
)

# Combined, sorted, for callers that want a single flat list (e.g. asserting the
# manifest declares every required runtime path).
REQUIRED_RUNTIME_PATHS: tuple[str, ...] = tuple(
    sorted(set(REQUIRED_RUNTIME_DIRS) | set(REQUIRED_RUNTIME_FILES))
)

_GLOB_CHARS = set("*?[")


def _logical_lines(dockerfile_text: str) -> list[str]:
    """Collapse backslash line continuations into single logical lines."""
    lines: list[str] = []
    buffer = ""
    for raw in dockerfile_text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            if buffer:
                # A comment inside a continuation is not valid Dockerfile; treat
                # the accumulated buffer as complete to stay conservative.
                lines.append(buffer)
                buffer = ""
            continue
        if stripped.endswith("\\"):
            buffer += stripped[:-1].rstrip() + " "
            continue
        buffer += stripped
        lines.append(buffer)
        buffer = ""
    if buffer:
        lines.append(buffer)
    return lines


def iter_copy_sources(dockerfile_text: str) -> list[str]:
    """Yield build-context source paths from every ``COPY`` in a Dockerfile.

    ``COPY --from=<stage>`` instructions are skipped: their sources come from a
    prior build stage, not the build context, so they place no requirement on
    the shipped tree. ``ADD`` is not used by these Dockerfiles and is ignored.
    """
    sources: list[str] = []
    for line in _logical_lines(dockerfile_text):
        try:
            tokens = shlex.split(line)
        except ValueError:
            # Unbalanced quotes: fall back to whitespace splitting.
            tokens = line.split()
        if not tokens or tokens[0].upper() != "COPY":
            continue
        args = tokens[1:]
        if any(arg.startswith("--from=") for arg in args):
            continue
        # Drop flags such as --chown=, --chmod=, --link.
        args = [arg for arg in args if not arg.startswith("--")]
        if len(args) < 2:
            continue
        # JSON-array form: COPY ["src", ..., "dest"].
        if args[0].lstrip().startswith("["):
            try:
                parsed = json.loads(" ".join(args))
            except json.JSONDecodeError:
                parsed = args
            if isinstance(parsed, list) and len(parsed) >= 2:
                args = [str(item) for item in parsed]
        # All arguments but the final destination are sources.
        sources.extend(args[:-1])
    return sources


def _dir_is_nonempty(path: Path) -> bool:
    return path.is_dir() and any(path.rglob("*"))


def verify_runtime_contexts(package_root: str | Path) -> list[str]:
    """Return a list of human-readable issues; empty means the package is sound.

    Checks, in order:
      * every required directory exists and is non-empty;
      * every required file exists;
      * every ``COPY`` source in every shipped Dockerfile resolves inside its
        declared build context (drift guard).
    """
    root = Path(package_root)
    issues: list[str] = []

    for rel in REQUIRED_RUNTIME_DIRS:
        path = root / rel
        if not path.exists():
            issues.append(f"required runtime directory missing: {rel}")
        elif not path.is_dir():
            issues.append(f"required runtime path is not a directory: {rel}")
        elif not _dir_is_nonempty(path):
            issues.append(f"required runtime directory is empty: {rel}")

    for rel in REQUIRED_RUNTIME_FILES:
        path = root / rel
        if not path.is_file():
            issues.append(f"required runtime file missing: {rel}")

    for build in RUNTIME_BUILD_CONTEXTS:
        dockerfile = root / build.dockerfile
        if not dockerfile.is_file():
            issues.append(f"build context Dockerfile missing: {build.dockerfile}")
            continue
        context_root = root / build.context
        for source in iter_copy_sources(dockerfile.read_text(encoding="utf-8")):
            resolved = context_root / source
            if _GLOB_CHARS & set(source):
                if not any(context_root.glob(source)):
                    issues.append(
                        f"{build.service}: Dockerfile COPY source has no match "
                        f"in context {build.context}: {source}"
                    )
            elif not resolved.exists():
                issues.append(
                    f"{build.service}: Dockerfile COPY source missing from "
                    f"context {build.context}: {source}"
                )

    return issues


def _package_root_from_zip_names(names: list[str]) -> str:
    """Return the single top-level directory shared by every archive entry."""
    roots = {name.split("/", 1)[0] for name in names if name}
    if len(roots) != 1:
        raise ValueError(f"archive does not have a single root directory: {sorted(roots)}")
    return roots.pop()


def verify_runtime_contexts_in_zip_names(names: list[str]) -> list[str]:
    """Presence-only check of required runtime paths against a ZIP name list.

    Used by the RC schema gate, which inspects ``namelist()`` without extracting.
    Directory entries may or may not be present in a ZIP, so a directory is
    considered shipped when at least one file lives under it.
    """
    root = _package_root_from_zip_names(names)
    members = {name for name in names}
    file_members = {name for name in names if not name.endswith("/")}
    issues: list[str] = []

    for rel in REQUIRED_RUNTIME_DIRS:
        prefix = f"{root}/{rel}/"
        if not any(member.startswith(prefix) for member in file_members):
            issues.append(f"required runtime directory missing from archive: {rel}")

    for rel in REQUIRED_RUNTIME_FILES:
        target = f"{root}/{rel}"
        if target not in members:
            issues.append(f"required runtime file missing from archive: {rel}")

    return issues
