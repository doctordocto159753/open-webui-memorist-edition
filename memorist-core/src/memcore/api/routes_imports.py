from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from memcore.config import get_settings
from memcore.heritage.package import (
    export_heritage,
    inspect_heritage,
    verify_heritage,
)
from memcore.imports.service import ImportService
from memcore.repositories.domain import RepositoryError
from memcore.storage.migrations import apply_migrations
from memcore.storage.sqlite import connect
from memcore.storage.write_commands.heritage_commands import restore_heritage_via_actor
from memcore.storage.write_commands.import_commands import commit_import_via_actor

router = APIRouter(prefix="/memcore", tags=["imports"])


class ImportUploadRequest(BaseModel):
    archive_path: str
    mode: str = "inspect"
    options: dict[str, Any] = {}
    target_workspace_uuid: str | None = None
    target_project_uuid: str | None = None


class ImportReconstructRequest(BaseModel):
    adapter_id: str | None = None


class ImportCommitRequest(BaseModel):
    processing_mode: str = "none"


class HeritageExportRequest(BaseModel):
    output_zip: str
    export_mode: str = "full"
    privacy_profile: str = "private-full"


class HeritageRestoreRequest(BaseModel):
    package_path: str
    db_path: str
    dry_run: bool = True


@router.post("/imports/upload", response_model=None)
def upload_import(request: ImportUploadRequest) -> dict[str, Any]:
    with _connection() as connection:
        return _guard(
            lambda: ImportService(connection, get_settings().object_store_path).upload(
                request.archive_path,
                request.mode,
                request.options,
                request.target_workspace_uuid,
                request.target_project_uuid,
            )
        )


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
def import_dry_run(import_run_uuid: str) -> dict[str, Any]:
    with _connection() as connection:
        return _guard(
            lambda: ImportService(connection, get_settings().object_store_path).dry_run(
                import_run_uuid
            )
        )


@router.get("/imports/{import_run_uuid}/dry-run-report", response_model=None)
def import_dry_run_report(import_run_uuid: str) -> dict[str, Any]:
    with _connection() as connection:
        return _guard(
            lambda: ImportService(connection, get_settings().object_store_path).dry_run_report(
                import_run_uuid
            )
        )


@router.post("/imports/{import_run_uuid}/commit", response_model=None)
def import_commit(import_run_uuid: str, request: ImportCommitRequest) -> dict[str, Any]:
    settings = get_settings()
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
    settings = get_settings()
    connection = connect(settings.db_path)
    try:
        apply_migrations(connection)
        yield connection
    finally:
        connection.close()


def _guard[ReturnT](callable_: Callable[[], ReturnT]) -> ReturnT:
    try:
        return callable_()
    except (RepositoryError, ValueError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
