from collections.abc import Iterator
from contextlib import contextmanager
from hashlib import sha256
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from memcore.config import get_settings
from memcore.governance.delivery import DeliveryTraceService
from memcore.model_control.repository import ModelControlRepository
from memcore.model_control.schemas import UsageEventCreate
from memcore.models import ModelRole
from memcore.preflight import PreflightRequest, PreflightService
from memcore.repositories import JobRepository, MemoryContextAttachmentRepository, MessageRepository
from memcore.repositories.retrieval import RetrievalRepository
from memcore.retrieval.runner import RetrievalRunner
from memcore.storage.sqlite import connect

router = APIRouter(prefix="/memcore", tags=["retrieval"])


class RetrievalRequest(BaseModel):
    session_uuid: str
    input_message_uuid: str
    retrieval_mode: str = "standard"
    token_budget: int = 1800


class PlanRequest(RetrievalRequest):
    pass


class AttachmentBuildRequest(RetrievalRequest):
    pass


class AssistantResponseCompletedRequest(BaseModel):
    input_message_uuid: str
    assistant_text: str
    attachment_uuid: str | None = None
    provider_response_id: str | None = None
    raw_payload: dict[str, Any] | None = None


@router.post("/retrieval/plan", response_model=None)
def plan_retrieval(request: PlanRequest) -> dict[str, Any]:
    settings = get_settings()
    with _connection() as connection:
        run, plan = RetrievalRunner(connection, settings).plan(
            request.session_uuid,
            request.input_message_uuid,
            request.retrieval_mode,
            request.token_budget,
        )
        return {
            "retrieval_run": run.model_dump(mode="json"),
            "plan": plan.model_dump(mode="json"),
        }


@router.post("/retrieval/run", response_model=None)
def run_retrieval(request: RetrievalRequest) -> dict[str, Any]:
    settings = get_settings()
    with _connection() as connection:
        run, plan, selection = RetrievalRunner(connection, settings).run(
            request.session_uuid,
            request.input_message_uuid,
            request.retrieval_mode,
            request.token_budget,
        )
        return {
            "retrieval_run": run.model_dump(mode="json"),
            "plan": plan.model_dump(mode="json"),
            "selected": [item.model_dump(mode="json") for item in selection.selected],
            "abstention_status": selection.abstention_status,
            "abstention_reason": selection.abstention_reason,
        }


@router.get("/retrieval/runs/{retrieval_run_uuid}", response_model=None)
def get_retrieval_run(retrieval_run_uuid: str) -> dict[str, Any]:
    with _connection() as connection:
        repository = RetrievalRepository(connection)
        run = repository.get_run(retrieval_run_uuid)
        if run is None:
            raise HTTPException(status_code=404, detail="retrieval run not found")
        queries = repository.list_queries(retrieval_run_uuid)
        return {
            "retrieval_run": run.model_dump(mode="json"),
            "queries": [query.model_dump(mode="json") for query in queries],
        }


@router.get("/retrieval/runs/{retrieval_run_uuid}/candidates", response_model=None)
def get_retrieval_candidates(retrieval_run_uuid: str) -> list[dict[str, Any]]:
    with _connection() as connection:
        return [
            candidate.model_dump(mode="json")
            for candidate in RetrievalRepository(connection).list_candidates(retrieval_run_uuid)
        ]


@router.post("/attachments/build", response_model=None)
def build_attachment(request: AttachmentBuildRequest) -> dict[str, Any]:
    settings = get_settings()
    with _connection() as connection:
        response = PreflightService(connection, settings).run(
            PreflightRequest(
                session_uuid=request.session_uuid,
                input_message_uuid=request.input_message_uuid,
                retrieval_mode=request.retrieval_mode,
                token_budget=request.token_budget,
            )
        )
        return response.model_dump(mode="json")


@router.get("/attachments/{attachment_uuid}", response_model=None)
def get_attachment(attachment_uuid: str) -> dict[str, Any]:
    with _connection() as connection:
        attachment = MemoryContextAttachmentRepository(connection).get_attachment(attachment_uuid)
        if attachment is None:
            raise HTTPException(status_code=404, detail="attachment not found")
        return attachment.model_dump(mode="json")


@router.get("/attachments/{attachment_uuid}/sources", response_model=None)
def get_attachment_sources(attachment_uuid: str) -> dict[str, Any]:
    with _connection() as connection:
        attachment = MemoryContextAttachmentRepository(connection).get_attachment(attachment_uuid)
        if attachment is None:
            raise HTTPException(status_code=404, detail="attachment not found")
        return {
            "attachment_uuid": attachment_uuid,
            "source_memory_uuids_ijson": attachment.source_memory_uuids_ijson,
            "source_block_uuids_ijson": attachment.source_block_uuids_ijson,
            "source_fact_edge_uuids_ijson": attachment.source_fact_edge_uuids_ijson,
        }


@router.post("/preflight", response_model=None)
def run_preflight(request: PreflightRequest) -> dict[str, Any]:
    settings = get_settings()
    with _connection() as connection:
        return PreflightService(connection, settings).run(request).model_dump(mode="json")


@router.post("/assistant-response/completed", response_model=None)
def assistant_response_completed(request: AssistantResponseCompletedRequest) -> dict[str, Any]:
    content_hash = sha256(request.assistant_text.encode("utf-8")).hexdigest()
    with _connection() as connection:
        messages = MessageRepository(connection)
        retrieval_repository = RetrievalRepository(connection)
        input_message = messages.get_message(request.input_message_uuid)
        if input_message is None:
            raise HTTPException(status_code=404, detail="input message not found")

        existing = retrieval_repository.get_assistant_response_link(
            request.input_message_uuid,
            content_hash,
            request.provider_response_id,
        )
        if existing is not None:
            return {
                "assistant_message_uuid": existing["assistant_message_uuid"],
                "response_link_uuid": existing["response_link_uuid"],
                "duplicate": True,
            }

        assistant_message = messages.create_message(
            input_message.session_uuid,
            role="assistant",
            creator_type="memory_augmented_model",
            raw_text=request.assistant_text,
            raw_payload=request.raw_payload,
        )
        link = retrieval_repository.record_assistant_response_link(
            input_message_uuid=request.input_message_uuid,
            assistant_message_uuid=assistant_message.message_uuid,
            attachment_uuid=request.attachment_uuid,
            provider_response_id=request.provider_response_id,
            content_hash=content_hash,
        )
        attachment = (
            MemoryContextAttachmentRepository(connection).get_attachment(request.attachment_uuid)
            if request.attachment_uuid
            else None
        )
        retrieval_repository.record_preflight_event(
            "assistant_response_completed",
            {
                "assistant_message_uuid": assistant_message.message_uuid,
                "provider_response_id_present": request.provider_response_id is not None,
            },
            session_uuid=input_message.session_uuid,
            input_message_uuid=request.input_message_uuid,
            attachment_uuid=request.attachment_uuid,
            retrieval_run_uuid=attachment.retrieval_run_uuid if attachment else None,
        )
        if request.attachment_uuid:
            connection.execute(
                """
                UPDATE memory_delivery_events
                SET response_message_uuid = ?
                WHERE attachment_uuid = ?
                """,
                (assistant_message.message_uuid, request.attachment_uuid),
            )
        model_control = ModelControlRepository(connection)
        extraction_default = model_control.resolve_default(ModelRole.MEMORY_EXTRACTION)
        extraction_profile_uuid = (
            str(extraction_default["model_profile_uuid"])
            if extraction_default is not None and extraction_default.get("model_profile_uuid")
            else None
        )
        job = JobRepository(connection).enqueue_job_once(
            "memory_extraction",
            {
                "message_uuid": assistant_message.message_uuid,
                "session_uuid": input_message.session_uuid,
                "model_role": ModelRole.MEMORY_EXTRACTION.value,
                "model_profile_uuid": extraction_profile_uuid,
            },
            priority=60,
        )
        model_control.record_usage_event(
            UsageEventCreate(
                role=ModelRole.MEMORY_EXTRACTION,
                stage="memory_extraction_queued",
                model_profile_uuid=extraction_profile_uuid,
                session_uuid=input_message.session_uuid,
                message_uuid=assistant_message.message_uuid,
                attachment_uuid=request.attachment_uuid,
                job_uuid=job.job_uuid,
                status="queued",
            )
        )
        DeliveryTraceService(connection).attribute_response(
            assistant_message.message_uuid,
            request.attachment_uuid,
            request.assistant_text,
        )
        return {
            "assistant_message_uuid": assistant_message.message_uuid,
            "response_link_uuid": link["response_link_uuid"],
            "duplicate": False,
        }


@contextmanager
def _connection() -> Iterator[Any]:
    settings = get_settings()
    connection = connect(settings.db_path)
    try:
        yield connection
    finally:
        connection.close()
