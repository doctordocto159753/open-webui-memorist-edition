from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from memcore.active_memory.compaction.compactor import BlockCompactor
from memcore.active_memory.compaction.scheduler import rebuild_stale_blocks, register_block_jobs
from memcore.active_memory.materializer import ActiveMemoryMaterializer
from memcore.active_memory.repositories import ActiveMemoryRepository
from memcore.config import get_settings
from memcore.governance.correction import MemoryCorrectionService
from memcore.governance.delivery import DeliveryTraceService
from memcore.governance.feedback import FeedbackService
from memcore.governance.inspection import MemoryInspectionService
from memcore.governance.privacy import PrivacyService
from memcore.governance.repositories import GovernanceRepository
from memcore.privacy.dependency_closure import privacy_request_closure
from memcore.privacy.residue_check import run_forget_residue_check
from memcore.repositories.domain import RepositoryError
from memcore.storage.migrations import apply_migrations
from memcore.storage.sqlite import connect
from memcore.storage.write_commands.privacy_commands import run_privacy_request_command

router = APIRouter(prefix="/memcore", tags=["governance"])


class BuildBlockRequest(BaseModel):
    trigger_type: str = "manual"
    actor_type: str = "user"
    expected_optimistic_lock_version: int | None = None


class FeedbackRequest(BaseModel):
    feedback_type: str
    actor_type: str
    memory_uuid: str | None = None
    memory_version_uuid: str | None = None
    attachment_uuid: str | None = None
    response_message_uuid: str | None = None
    rating: int | None = None
    comment: str | None = None
    actor_uuid: str | None = None


class ChangeRequestCreate(BaseModel):
    requested_operation: str
    proposed_value: dict[str, Any] | None = None
    reason: str | None = None
    actor_type: str = "user"
    actor_uuid: str | None = None


class PrivacyPreviewRequest(BaseModel):
    request_type: str
    target_type: str
    target_uuid: str | None = None
    requested_scope: dict[str, Any]
    actor_type: str = "user"
    actor_uuid: str | None = None


class PrivacyConfirmRequest(BaseModel):
    confirmation_token: str


class ForgetPreviewRequest(BaseModel):
    target_type: str
    target_uuid: str | None = None
    requested_scope: dict[str, Any]
    actor_type: str = "user"
    actor_uuid: str | None = None


@router.post("/blocks/{block_uuid}/build", response_model=None)
def build_block(block_uuid: str, request: BuildBlockRequest) -> dict[str, Any]:
    with _connection() as connection:
        return _guard(
            lambda: (
                ActiveMemoryMaterializer(connection)
                .build(
                    block_uuid,
                    request.trigger_type,
                    request.actor_type,
                    request.expected_optimistic_lock_version,
                )
                .model_dump(mode="json")
            )
        )


@router.get("/blocks/{block_uuid}/versions", response_model=None)
def block_versions(block_uuid: str) -> list[dict[str, Any]]:
    with _connection() as connection:
        return ActiveMemoryRepository(connection).list_versions(block_uuid)


@router.get("/blocks/{block_uuid}/sources", response_model=None)
def block_sources(block_uuid: str) -> list[dict[str, Any]]:
    with _connection() as connection:
        return ActiveMemoryRepository(connection).list_sources(block_uuid)


@router.post("/blocks/{block_uuid}/rollback/{version_number}", response_model=None)
def rollback_block(block_uuid: str, version_number: int) -> dict[str, Any]:
    with _connection() as connection:
        return _guard(
            lambda: ActiveMemoryRepository(connection).rollback(block_uuid, version_number)
        )


@router.post("/blocks/{block_uuid}/compact", response_model=None)
def compact_block(block_uuid: str, request: BuildBlockRequest | None = None) -> dict[str, Any]:
    actor_type = request.actor_type if request else "user"
    with _connection() as connection:
        return _guard(lambda: BlockCompactor(connection).compact(block_uuid, actor_type))


@router.post("/blocks/rebuild-stale", response_model=None)
def rebuild_stale() -> list[dict[str, object]]:
    with _connection() as connection:
        register_block_jobs(connection)
        return rebuild_stale_blocks(connection)


@router.get("/blocks/{block_uuid}/coverage", response_model=None)
def block_coverage(block_uuid: str) -> dict[str, Any]:
    with _connection() as connection:
        return ActiveMemoryMaterializer(connection).coverage(block_uuid).model_dump(mode="json")


@router.get("/blocks/{block_uuid}/diff", response_model=None)
def block_diff(block_uuid: str) -> dict[str, Any]:
    with _connection() as connection:
        versions = ActiveMemoryRepository(connection).list_versions(block_uuid)
        if len(versions) < 2:
            return {
                "block_uuid": block_uuid,
                "changed": False,
                "previous": None,
                "current": versions[-1] if versions else None,
            }
        return {
            "block_uuid": block_uuid,
            "changed": versions[-2]["value"] != versions[-1]["value"],
            "previous": versions[-2],
            "current": versions[-1],
        }


@router.get("/responses/{message_uuid}/memory-trace", response_model=None)
def response_memory_trace(message_uuid: str) -> dict[str, Any]:
    with _connection() as connection:
        return DeliveryTraceService(connection).response_trace(message_uuid)


@router.post("/memory-feedback", response_model=None)
def memory_feedback(request: FeedbackRequest) -> dict[str, Any]:
    with _connection() as connection:
        return FeedbackService(connection).submit_feedback(**request.model_dump())


@router.get("/memories/{memory_uuid}/usage", response_model=None)
def memory_usage(memory_uuid: str) -> dict[str, Any]:
    with _connection() as connection:
        return DeliveryTraceService(connection).memory_usage(memory_uuid)


@router.get("/attachments/{attachment_uuid}/delivery", response_model=None)
def attachment_delivery(attachment_uuid: str) -> list[dict[str, Any]]:
    with _connection() as connection:
        return DeliveryTraceService(connection).attachment_delivery(attachment_uuid)


@router.get("/memories/{memory_uuid}/inspect", response_model=None)
def inspect_memory(memory_uuid: str) -> dict[str, Any]:
    with _connection() as connection:
        return _guard(lambda: MemoryInspectionService(connection).inspect_memory(memory_uuid))


@router.post("/memories/{memory_uuid}/change-requests", response_model=None)
def create_memory_change_request(
    memory_uuid: str,
    request: ChangeRequestCreate,
) -> dict[str, Any]:
    with _connection() as connection:
        return _guard(
            lambda: MemoryCorrectionService(connection).create_request(
                memory_uuid,
                request.requested_operation,
                request.actor_type,
                request.proposed_value,
                request.reason,
                request.actor_uuid,
            )
        )


@router.get("/memory-change-requests/{request_uuid}", response_model=None)
def get_memory_change_request(request_uuid: str) -> dict[str, Any]:
    with _connection() as connection:
        return _guard(lambda: GovernanceRepository(connection).get_change_request(request_uuid))


@router.post("/memory-change-requests/{request_uuid}/apply", response_model=None)
def apply_memory_change_request(request_uuid: str) -> dict[str, Any]:
    with _connection() as connection:
        return _guard(lambda: MemoryCorrectionService(connection).apply_request(request_uuid))


@router.post("/memory-change-requests/{request_uuid}/cancel", response_model=None)
def cancel_memory_change_request(request_uuid: str) -> dict[str, Any]:
    with _connection() as connection:
        return _guard(lambda: MemoryCorrectionService(connection).cancel_request(request_uuid))


@router.post("/memory-change-requests/{request_uuid}/undo", response_model=None)
def undo_memory_change_request(request_uuid: str) -> dict[str, Any]:
    with _connection() as connection:
        return _guard(lambda: MemoryCorrectionService(connection).undo_request(request_uuid))


@router.post("/privacy/requests/preview", response_model=None)
def preview_privacy_request(request: PrivacyPreviewRequest) -> dict[str, Any]:
    with _connection() as connection:
        return _guard(lambda: PrivacyService(connection).preview_request(**request.model_dump()))


@router.post("/privacy/requests/{request_uuid}/confirm", response_model=None)
def confirm_privacy_request(request_uuid: str, request: PrivacyConfirmRequest) -> dict[str, Any]:
    settings = get_settings()
    return _guard(
        lambda: run_privacy_request_command(
            settings.db_path,
            request_uuid,
            "confirm",
            confirmation_value=request.confirmation_token,
        )
    )


@router.post("/privacy/requests/{request_uuid}/execute", response_model=None)
def execute_privacy_request(request_uuid: str) -> dict[str, Any]:
    settings = get_settings()
    return _guard(
        lambda: run_privacy_request_command(settings.db_path, request_uuid, "execute")
    )


@router.get("/privacy/requests/{request_uuid}", response_model=None)
def get_privacy_request(request_uuid: str) -> dict[str, Any]:
    with _connection() as connection:
        return _guard(lambda: GovernanceRepository(connection).get_privacy_request(request_uuid))


@router.get("/privacy/requests/{request_uuid}/receipt", response_model=None)
def get_privacy_receipt(request_uuid: str) -> dict[str, Any]:
    with _connection() as connection:
        receipt = GovernanceRepository(connection).get_receipt(request_uuid)
        if receipt is None:
            raise HTTPException(status_code=404, detail="receipt not found")
        return receipt


@router.post("/privacy/requests/{request_uuid}/retry", response_model=None)
def retry_privacy_request(request_uuid: str) -> dict[str, Any]:
    settings = get_settings()
    return _guard(lambda: run_privacy_request_command(settings.db_path, request_uuid, "retry"))


@router.post("/privacy/forget/preview", response_model=None)
def preview_forget_request(request: ForgetPreviewRequest) -> dict[str, Any]:
    with _connection() as connection:
        return _guard(
            lambda: PrivacyService(connection).preview_request(
                request_type="forget",
                target_type=request.target_type,
                target_uuid=request.target_uuid,
                requested_scope=request.requested_scope,
                actor_type=request.actor_type,
                actor_uuid=request.actor_uuid,
            )
        )


@router.post("/privacy/forget/{request_uuid}/confirm", response_model=None)
def confirm_forget_request(request_uuid: str, request: PrivacyConfirmRequest) -> dict[str, Any]:
    return confirm_privacy_request(request_uuid, request)


@router.post("/privacy/forget/{request_uuid}/execute", response_model=None)
def execute_forget_request(request_uuid: str) -> dict[str, Any]:
    return execute_privacy_request(request_uuid)


@router.get("/privacy/forget/{request_uuid}/closure", response_model=None)
def get_forget_closure(request_uuid: str) -> dict[str, Any]:
    with _connection() as connection:
        return _guard(lambda: privacy_request_closure(connection, request_uuid))


@router.get("/privacy/forget/{request_uuid}/residue", response_model=None)
def get_forget_residue(
    request_uuid: str,
    forbidden_text: str | None = None,
) -> dict[str, Any]:
    with _connection() as connection:
        request = GovernanceRepository(connection).get_privacy_request(request_uuid)
        if not request["target_uuid"]:
            raise HTTPException(status_code=400, detail="privacy request has no target_uuid")
        return run_forget_residue_check(connection, request["target_uuid"], forbidden_text)


@router.get("/privacy/forget/{request_uuid}/receipt", response_model=None)
def get_forget_receipt(request_uuid: str) -> dict[str, Any]:
    return get_privacy_receipt(request_uuid)


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
    except RepositoryError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
