import hashlib
import mimetypes
import shutil
import zipfile
from pathlib import Path

from memcore.imports.models import StagedArtifact
from memcore.imports.security import (
    MAX_EXPANDED_BYTES,
    ImportSecurityError,
    validate_zip_archive,
)


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stage_archive(
    archive_path: str, staging_root: str | Path, import_run_uuid: str
) -> list[StagedArtifact]:
    validate_zip_archive(archive_path)
    run_dir = Path(staging_root) / "imports" / import_run_uuid
    object_dir = run_dir / "objects"
    object_dir.mkdir(parents=True, exist_ok=True)
    artifacts: list[StagedArtifact] = []
    with zipfile.ZipFile(archive_path) as archive:
        for index, info in enumerate(archive.infolist()):
            if info.is_dir():
                continue
            internal_name = f"artifact-{index:06d}{Path(info.filename).suffix.lower()}"
            target_path = object_dir / internal_name
            with archive.open(info) as source, target_path.open("wb") as target:
                shutil.copyfileobj(source, target, length=1024 * 1024)
            digest = sha256_file(target_path)
            media_type = mimetypes.guess_type(info.filename)[0]
            artifacts.append(
                StagedArtifact(
                    relative_path=info.filename.replace("\\", "/"),
                    object_store_path=str(target_path),
                    artifact_role=_artifact_role(info.filename),
                    detected_media_type=media_type,
                    size_bytes=info.file_size,
                    sha256=digest,
                )
            )
    return artifacts


def stage_import_source(
    source_path: str, staging_root: str | Path, import_run_uuid: str
) -> list[StagedArtifact]:
    """Stage a supported import source without trusting its original path or filename."""
    source = Path(source_path)
    if not source.is_file():
        raise ImportSecurityError("import source is not a regular file")
    suffix = source.suffix.lower()
    if suffix == ".zip":
        return stage_archive(str(source), staging_root, import_run_uuid)
    if suffix not in {".json", ".jsonl"}:
        raise ImportSecurityError("unsupported import source; expected .zip, .json, or .jsonl")
    size_bytes = source.stat().st_size
    if size_bytes > MAX_EXPANDED_BYTES:
        raise ImportSecurityError("standalone import source exceeds expanded size limit")

    object_dir = Path(staging_root) / "imports" / import_run_uuid / "objects"
    object_dir.mkdir(parents=True, exist_ok=True)
    target_path = object_dir / f"artifact-000000{suffix}"
    with source.open("rb") as input_file, target_path.open("wb") as output_file:
        shutil.copyfileobj(input_file, output_file, length=1024 * 1024)
    return [
        StagedArtifact(
            relative_path=source.name,
            object_store_path=str(target_path),
            artifact_role="conversation_data",
            detected_media_type=mimetypes.guess_type(source.name)[0] or "application/json",
            size_bytes=size_bytes,
            sha256=sha256_file(target_path),
        )
    ]


def _artifact_role(filename: str) -> str:
    lowered = filename.lower()
    if "manifest" in lowered:
        return "manifest"
    if lowered.endswith((".json", ".jsonl")):
        return "conversation_data"
    if lowered.endswith((".html", ".htm")):
        return "html_activity"
    if lowered.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".mp4")):
        return "generated_media"
    return "unknown"
