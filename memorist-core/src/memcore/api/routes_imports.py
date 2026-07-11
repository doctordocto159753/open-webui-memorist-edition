import hashlib
import os
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from memcore.config import get_settings
from memcore.heritage.package import (
    export_heritage,
    inspect_heritage,
    verify_heritage,
)
from memcore.imports.runtime import import_connection
from memcore.imports.security import (
    MAX_COMPRESSED_BYTES,
    MAX_EXPANDED_BYTES,
    ImportSecurityError,
    validate_zip_archive,
)
from memcore.imports.service import ImportService
from memcore.imports.staging import sanitize_upload_filename
from memcore.repositories.domain import RepositoryError
from memcore.storage.write_commands.heritage_commands import restore_heritage_via_actor
from memcore.storage.write_commands.import_commands import (
    commit_import_via_actor,
    process_import_batch_via_actor,
)
from memcore.validators.ijson import load_ijson

router = APIRouter(prefix="/memcore", tags=["imports"])


class ImportReconstructRequest(BaseModel):
    adapter_id: str | None = None


class ImportCommitRequest(BaseModel):
    processing_mode: str = "none"


class ImportDryRunRequest(BaseModel):
    processing_mode: str | None = None


class ImportProcessRequest(BaseModel):
    batch_size: int = Field(default=25, ge=1, le=1_000)


class HeritageExportRequest(BaseModel):
    output_zip: str
    export_mode: str = "full"
    privacy_profile: str = "private-full"


class HeritageRestoreRequest(BaseModel):
    package_path: str
    db_path: str
    dry_run: bool = True


@router.get("/imports", response_model=None)
def list_imports(limit: int = 25) -> dict[str, Any]:
    with _connection() as connection:
        service = ImportService(connection, get_settings().object_store_path)
        return {"items": service.repository.list_runs(limit)}


@router.post("/imports/upload-file", response_model=None)
def upload_import_file(
    file: Annotated[UploadFile, File()],
    mode: Annotated[str, Form()] = "inspect",
    processing_mode: Annotated[str | None, Form()] = None,
    target_workspace_uuid: Annotated[str | None, Form()] = None,
    target_project_uuid: Annotated[str | None, Form()] = None,
) -> dict[str, Any]:
    safe_name = sanitize_upload_filename(file.filename)
    suffix = Path(safe_name).suffix.lower()
    if suffix not in {".zip", ".json", ".jsonl"}:
        raise HTTPException(
            status_code=400,
            detail="Unsupported import file type. Choose a .zip, .json, or .jsonl export.",
        )
    byte_limit = MAX_COMPRESSED_BYTES if suffix == ".zip" else MAX_EXPANDED_BYTES
    settings = get_settings()
    temp_dir = Path(settings.object_store_path) / "imports" / "_uploads"
    temp_dir.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix="upload-", suffix=suffix, dir=temp_dir)
    temp_path = Path(temp_name)
    digest = hashlib.sha256()
    total = 0
    try:
        with os.fdopen(fd, "wb") as output:
            while chunk := file.file.read(1024 * 1024):
                total += len(chunk)
                if total > byte_limit:
                    raise ImportSecurityError("uploaded import file exceeds size limit")
                digest.update(chunk)
                output.write(chunk)
        if suffix == ".zip":
            validate_zip_archive(str(temp_path))
        with _connection() as connection:
            return _guard(
                lambda: ImportService(connection, settings.object_store_path).upload_staged_file(
                    str(temp_path),
                    digest.hexdigest(),
                    safe_name,
                    mode,
                    {"processing_mode": processing_mode} if processing_mode else {},
                    target_workspace_uuid,
                    target_project_uuid,
                )
            )
    except (ImportSecurityError, ValueError) as error:
        raise HTTPException(status_code=400, detail=_sanitize_upload_error(str(error))) from error
    finally:
        try:
            file.file.close()
        finally:
            if temp_path.exists():
                temp_path.unlink()


@router.post("/imports/{import_run_uuid}/inspect", response_model=None)
def inspect_import(import_run_uuid: str) -> dict[str, Any]:
    with _connection() as connection:
        return _guard(
            lambda: ImportService(connection, get_settings().object_store_path).inspect(
                import_run_uuid
            )
        )


@router.get("/imports/{import_run_uuid}", response_model=None)
def get_import(import_run_uuid: str) -> dict[str, Any]:
    with _connection() as connection:
        return _guard(
            lambda: ImportService(connection, get_settings().object_store_path).repository.get_run(
                import_run_uuid
            )
        )


@router.get("/imports/{import_run_uuid}/issues", response_model=None)
def get_import_issues(import_run_uuid: str) -> list[dict[str, Any]]:
    with _connection() as connection:
        return ImportService(connection, get_settings().object_store_path).repository.list_issues(
            import_run_uuid
        )


@router.delete("/imports/{import_run_uuid}/staging", response_model=None)
def delete_import_staging(import_run_uuid: str) -> dict[str, str]:
    with _connection() as connection:
        return ImportService(connection, get_settings().object_store_path).delete_staging(
            import_run_uuid
        )


@router.post("/imports/{import_run_uuid}/reconstruct", response_model=None)
def reconstruct_import(import_run_uuid: str, request: ImportReconstructRequest) -> dict[str, Any]:
    with _connection() as connection:
        return _guard(
            lambda: ImportService(connection, get_settings().object_store_path).reconstruct(
                import_run_uuid,
                request.adapter_id,
            )
        )


@router.get("/imports/{import_run_uuid}/conversations", response_model=None)
def import_conversations(import_run_uuid: str) -> list[dict[str, Any]]:
    with _connection() as connection:
        return ImportService(connection, get_settings().object_store_path).conversations(
            import_run_uuid
        )


@router.get("/imports/{import_run_uuid}/conversations/{source_id}/preview", response_model=None)
def import_conversation_preview(import_run_uuid: str, source_id: str) -> dict[str, Any]:
    with _connection() as connection:
        return _guard(
            lambda: ImportService(
                connection, get_settings().object_store_path
            ).preview_conversation(
                import_run_uuid,
                source_id,
            )
        )


@router.post("/imports/{import_run_uuid}/dry-run", response_model=None)
def import_dry_run(
    import_run_uuid: str, request: ImportDryRunRequest | None = None
) -> dict[str, Any]:
    with _connection() as connection:
        return _guard(
            lambda: ImportService(connection, get_settings().object_store_path).dry_run(
                import_run_uuid, request.processing_mode if request else None
            )
        )


@router.get("/imports/{import_run_uuid}/dry-run-report", response_model=None)
def import_dry_run_report(import_run_uuid: str) -> dict[str, Any]:
    with _connection() as connection:
        row = _guard(
            lambda: ImportService(connection, get_settings().object_store_path).dry_run_report(
                import_run_uuid
            )
        )
    report = load_ijson(str(row["report_ijson"]))
    if not isinstance(report, dict):
        raise HTTPException(status_code=500, detail="Dry-run report payload is invalid")
    return {
        "import_run_uuid": row["import_run_uuid"],
        "plan_fingerprint": row["plan_fingerprint"],
        "created_at": row["created_at"],
        **report,
    }


@router.post("/imports/{import_run_uuid}/commit", response_model=None)
def import_commit(import_run_uuid: str, request: ImportCommitRequest) -> dict[str, Any]:
    settings = get_settings()
    if settings.runtime_profile == "full" and settings.canonical_store == "postgres":
        with _connection() as connection:
            return _guard(
                lambda: ImportService(connection, settings.object_store_path).commit(
                    import_run_uuid,
                    request.processing_mode,
                    batch_size=settings.import_batch_size,
                    max_write_queue_depth=settings.import_max_write_queue_depth,
                )
            )
    return _guard(
        lambda: commit_import_via_actor(
            settings.db_path,
            settings.object_store_path,
            import_run_uuid,
            request.processing_mode,
            batch_size=settings.import_batch_size,
            max_write_queue_depth=settings.import_max_write_queue_depth,
        )
    )


@router.get("/imports/{import_run_uuid}/progress", response_model=None)
def import_progress(import_run_uuid: str) -> dict[str, Any]:
    with _connection() as connection:
        return _guard(
            lambda: ImportService(connection, get_settings().object_store_path).progress(
                import_run_uuid
            )
        )


@router.post("/imports/{import_run_uuid}/process", response_model=None)
def import_process(import_run_uuid: str, request: ImportProcessRequest) -> dict[str, Any]:
    settings = get_settings()
    if settings.runtime_profile == "full" and settings.canonical_store == "postgres":
        with _connection() as connection:
            return _guard(
                lambda: ImportService(connection, settings.object_store_path).process_next_batch(
                    import_run_uuid, request.batch_size
                )
            )
    return _guard(
        lambda: process_import_batch_via_actor(
            settings.db_path,
            settings.object_store_path,
            import_run_uuid,
            request.batch_size,
        )
    )


@router.post("/imports/{import_run_uuid}/retry-failed", response_model=None)
def import_retry_failed(import_run_uuid: str) -> dict[str, Any]:
    with _connection() as connection:
        return _guard(
            lambda: ImportService(
                connection, get_settings().object_store_path
            ).retry_failed_processing(import_run_uuid)
        )


@router.get("/imports/{import_run_uuid}/processing-report", response_model=None)
def import_processing_report(import_run_uuid: str) -> dict[str, Any]:
    with _connection() as connection:
        return _guard(
            lambda: ImportService(connection, get_settings().object_store_path).processing_report(
                import_run_uuid
            )
        )


@router.get("/imports/{import_run_uuid}/messages/processing-status", response_model=None)
def import_message_processing_status(import_run_uuid: str) -> list[dict[str, Any]]:
    with _connection() as connection:
        return _guard(
            lambda: ImportService(
                connection, get_settings().object_store_path
            ).message_processing_statuses(import_run_uuid)
        )


@router.post("/imports/{import_run_uuid}/pause", response_model=None)
def import_pause(import_run_uuid: str) -> dict[str, Any]:
    with _connection() as connection:
        return _guard(
            lambda: ImportService(connection, get_settings().object_store_path).pause(
                import_run_uuid
            )
        )


@router.post("/imports/{import_run_uuid}/resume", response_model=None)
def import_resume(import_run_uuid: str) -> dict[str, Any]:
    with _connection() as connection:
        return _guard(
            lambda: ImportService(connection, get_settings().object_store_path).resume(
                import_run_uuid
            )
        )


@router.post("/imports/{import_run_uuid}/cancel", response_model=None)
def import_cancel(import_run_uuid: str) -> dict[str, Any]:
    with _connection() as connection:
        return _guard(
            lambda: ImportService(connection, get_settings().object_store_path).cancel(
                import_run_uuid
            )
        )


@router.post("/heritage/export", response_model=None)
def heritage_export(request: HeritageExportRequest) -> dict[str, Any]:
    with _connection() as connection:
        return export_heritage(
            connection, request.output_zip, request.export_mode, request.privacy_profile
        )


@router.get("/heritage/verify", response_model=None)
def heritage_verify(package_path: str) -> dict[str, Any]:
    return verify_heritage(package_path)


@router.get("/heritage/inspect", response_model=None)
def heritage_inspect(package_path: str) -> dict[str, Any]:
    return inspect_heritage(package_path)


@router.post("/heritage/restore", response_model=None)
def heritage_restore(request: HeritageRestoreRequest) -> dict[str, Any]:
    return restore_heritage_via_actor(request.package_path, request.db_path, request.dry_run)


@contextmanager
def _connection() -> Iterator[Any]:
    with import_connection(get_settings()) as connection:
        yield connection


def _guard[ReturnT](callable_: Callable[[], ReturnT]) -> ReturnT:
    try:
        return callable_()
    except (RepositoryError, ValueError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


def _sanitize_upload_error(message: str) -> str:
    lowered = message.lower()
    if "traversal" in lowered:
        return "Archive contains a path traversal entry and was rejected."
    if "absolute path" in lowered:
        return "Archive contains an absolute path entry and was rejected."
    if "link or device" in lowered:
        return "Archive contains an unsupported link or device entry and was rejected."
    if "too many files" in lowered:
        return "Archive contains too many files."
    if "compression ratio" in lowered:
        return "Archive compression ratio exceeds the safety limit."
    if "size" in lowered or "exceeds" in lowered:
        return "Uploaded import file exceeds the configured size limit."
    if "not a zip" in lowered or "badzip" in lowered:
        return "Uploaded ZIP archive is malformed."
    return "Import upload failed validation."
