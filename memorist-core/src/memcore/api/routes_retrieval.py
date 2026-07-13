import json
from hashlib import sha256
from typing import Any

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, ConfigDict

from memcore.config import Settings, get_settings
from memcore.governance.delivery import DeliveryTraceService
from memcore.memory_control import MemoryControlRepository, memory_control_connection
from memcore.memory_control.full_retrieval import FullPostgresPreflightService
from memcore.memory_control.policy import MemoristTurnPolicy, normalize_turn_policy
from memcore.model_control.repository import ModelControlRepository
from memcore.model_control.schemas import UsageEventCreate
from memcore.models import ModelRole, PreflightResponse, PreflightStatus, new_uuid, utc_now
from memcore.preflight import PreflightRequest, PreflightService
from memcore.repositories import JobRepository, MemoryContextAttachmentRepository, MessageRepository
from memcore.repositories.retrieval import RetrievalRepository
from memcore.retrieval.runner import RetrievalRunner
from memcore.validators.ijson import dump_ijson

router = APIRouter(prefix="/memcore", tags=["retrieval"])


class RetrievalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_uuid: str
    input_message_uuid: str
    retrieval_mode: str = "standard"
    token_budget: int = 1800
    turn_policy: str | None = None
    user_uuid: str | None = None
    workspace_uuid: str | None = None
    attachment_review: bool = False


class PlanRequest(RetrievalRequest):
    pass


class AttachmentBuildRequest(RetrievalRequest):
    pass


class AssistantResponseCompletedRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    input_message_uuid: str
    assistant_text: str
    attachment_uuid: str | None = None
    provider_response_id: str | None = None
    raw_payload: dict[str, Any] | None = None
    turn_policy: str | None = None
    user_uuid: str | None = None
    workspace_uuid: str | None = None
    regeneration_uuid: str | None = None


@router.post("/retrieval/plan", response_model=None)
def plan_retrieval(request: PlanRequest) -> dict[str, Any]:
    settings = get_settings()
    if settings.runtime_profile == "full":
        return _run_controlled_preflight(settings, _as_preflight(request)).model_dump(mode="json")
    with memory_control_connection(settings) as connection:
        policy, _contract = _policy_for_input(connection, settings, _as_preflight(request))
        if not policy.recall_enabled:
            return {"status": "disabled", "turn_policy": policy.mode.value}
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
    if settings.runtime_profile == "full":
        return _run_controlled_preflight(settings, _as_preflight(request)).model_dump(mode="json")
    with memory_control_connection(settings) as connection:
        policy, _contract = _policy_for_input(connection, settings, _as_preflight(request))
        if not policy.recall_enabled:
            return {"status": "disabled", "turn_policy": policy.mode.value}
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
    settings = get_settings()
    with memory_control_connection(settings) as connection:
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
    settings = get_settings()
    with memory_control_connection(settings) as connection:
        return [
            candidate.model_dump(mode="json")
            for candidate in RetrievalRepository(connection).list_candidates(retrieval_run_uuid)
        ]


@router.post("/attachments/build", response_model=None)
def build_attachment(request: AttachmentBuildRequest) -> dict[str, Any]:
    settings = get_settings()
    return _run_controlled_preflight(settings, _as_preflight(request)).model_dump(mode="json")


@router.get("/attachments/{attachment_uuid}", response_model=None)
def get_attachment(
    attachment_uuid: str,
    x_memorist_user_id: str | None = Header(default=None),
    x_memorist_workspace_id: str | None = Header(default=None),
) -> dict[str, Any]:
    if not x_memorist_user_id:
        raise HTTPException(status_code=401, detail="Memorist actor identity required")
    settings = get_settings()
    with memory_control_connection(settings) as connection:
        attachment = MemoryControlRepository(
            connection, settings.runtime_profile
        ).attachment_for_actor(
            attachment_uuid,
            user_uuid=x_memorist_user_id,
            workspace_uuid=x_memorist_workspace_id,
        )
        if attachment is None:
            raise HTTPException(status_code=404, detail="attachment not found")
        return attachment


@router.get("/attachments/{attachment_uuid}/sources", response_model=None)
def get_attachment_sources(
    attachment_uuid: str,
    x_memorist_user_id: str | None = Header(default=None),
    x_memorist_workspace_id: str | None = Header(default=None),
) -> dict[str, Any]:
    attachment = get_attachment(
        attachment_uuid,
        x_memorist_user_id=x_memorist_user_id,
        x_memorist_workspace_id=x_memorist_workspace_id,
    )
    return {
        "attachment_uuid": attachment_uuid,
        "source_memory_uuids_ijson": attachment.get("source_memory_uuids_ijson"),
        "source_block_uuids_ijson": attachment.get("source_block_uuids_ijson"),
        "source_fact_edge_uuids_ijson": attachment.get("source_fact_edge_uuids_ijson"),
    }


@router.post("/preflight", response_model=None)
def run_preflight(request: PreflightRequest) -> dict[str, Any]:
    settings = get_settings()
    return _run_controlled_preflight(settings, request).model_dump(mode="json")


@router.post("/assistant-response/completed", response_model=None)
def assistant_response_completed(request: AssistantResponseCompletedRequest) -> dict[str, Any]:
    settings = get_settings()
    if settings.runtime_profile == "full":
        return _assistant_response_completed_full(settings, request)
    content_hash = sha256(request.assistant_text.encode("utf-8")).hexdigest()
    with memory_control_connection(settings) as connection:
        policy, contract = _policy_for_input(connection, settings, request)
        if policy.private:
            return {"skipped": True, "turn_policy": "private"}
        control = MemoryControlRepository(connection, settings.runtime_profile)
        attachment_row: dict[str, Any] | None = None
        actor_user = request.user_uuid or (
            str(contract.get("user_uuid")) if contract and contract.get("user_uuid") else None
        )
        actor_workspace = request.workspace_uuid or (
            str(contract.get("workspace_uuid"))
            if contract and contract.get("workspace_uuid")
            else None
        )
        if request.attachment_uuid:
            if not policy.attachment_enabled:
                raise HTTPException(status_code=409, detail="turn policy forbids attachments")
            if not actor_user:
                raise HTTPException(status_code=401, detail="attachment actor identity required")
            attachment_row = control.attachment_for_actor(
                request.attachment_uuid,
                user_uuid=actor_user,
                workspace_uuid=actor_workspace,
            )
            if (
                attachment_row is None
                or attachment_row.get("input_message_uuid") != request.input_message_uuid
            ):
                raise HTTPException(status_code=404, detail="attachment not found")
            if attachment_row.get("lifecycle_status") != "delivered":
                raise HTTPException(
                    status_code=409,
                    detail="attachment was not delivered for this response",
                )
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
        if request.attachment_uuid and attachment_row is not None and actor_user:
            control.transition_attachment(
                request.attachment_uuid,
                "used_for_response",
                user_uuid=actor_user,
                workspace_uuid=actor_workspace,
                idempotency_key=f"response:{link['response_link_uuid']}",
                response_message_uuid=assistant_message.message_uuid,
            )
        if request.regeneration_uuid:
            connection.execute(
                """
                UPDATE memorist_regenerations
                SET regenerated_assistant_message_uuid = ?, status = 'completed',
                    completed_at = CURRENT_TIMESTAMP
                WHERE regeneration_uuid = ? AND original_input_message_uuid = ?
                """,
                (
                    assistant_message.message_uuid,
                    request.regeneration_uuid,
                    request.input_message_uuid,
                ),
            )
            connection.commit()
        return {
            "assistant_message_uuid": assistant_message.message_uuid,
            "response_link_uuid": link["response_link_uuid"],
            "duplicate": False,
        }


def _as_preflight(request: RetrievalRequest) -> PreflightRequest:
    return PreflightRequest(
        session_uuid=request.session_uuid,
        input_message_uuid=request.input_message_uuid,
        retrieval_mode=request.retrieval_mode,
        token_budget=request.token_budget,
        turn_policy=request.turn_policy,
        user_uuid=request.user_uuid,
        workspace_uuid=request.workspace_uuid,
        attachment_review=request.attachment_review,
    )


def _policy_for_input(
    connection: Any,
    settings: Settings,
    request: PreflightRequest | AssistantResponseCompletedRequest,
) -> tuple[MemoristTurnPolicy, dict[str, Any] | None]:
    repository = MemoryControlRepository(connection, settings.runtime_profile)
    regeneration_uuid = getattr(request, "regeneration_uuid", None)
    if regeneration_uuid:
        regeneration = connection.execute(
            "SELECT * FROM memorist_regenerations WHERE regeneration_uuid = ?",
            (regeneration_uuid,),
        ).fetchone()
        if (
            regeneration is None
            or regeneration["original_input_message_uuid"] != request.input_message_uuid
        ):
            raise HTTPException(status_code=404, detail="regeneration request not found")
        if request.user_uuid and regeneration["user_uuid"] not in {None, request.user_uuid}:
            raise HTTPException(status_code=403, detail="regeneration user scope mismatch")
        if request.workspace_uuid and regeneration["workspace_uuid"] != request.workspace_uuid:
            raise HTTPException(status_code=403, detail="regeneration workspace scope mismatch")
        return normalize_turn_policy("no_recall"), repository.get_turn_contract(
            request.input_message_uuid
        )
    contract = repository.get_turn_contract(request.input_message_uuid)
    if contract is not None:
        if request.user_uuid and contract.get("user_uuid") not in {None, request.user_uuid}:
            raise HTTPException(status_code=403, detail="user scope mismatch")
        if request.workspace_uuid and contract.get("workspace_uuid") != request.workspace_uuid:
            raise HTTPException(status_code=403, detail="workspace scope mismatch")
        return normalize_turn_policy(str(contract["turn_policy"])), contract
    return normalize_turn_policy(request.turn_policy or settings.default_turn_policy), None


def _run_controlled_preflight(settings: Settings, request: PreflightRequest) -> PreflightResponse:
    with memory_control_connection(settings) as connection:
        policy, contract = _policy_for_input(connection, settings, request)
        attachment_review = (
            bool(contract.get("attachment_review")) if contract else request.attachment_review
        )
        if policy.private or not policy.recall_enabled or not policy.attachment_enabled:
            return PreflightResponse(
                status=PreflightStatus.DISABLED,
                latency_ms=0,
                attachment_mode="disabled",
                budget_reason="turn policy disables Memorist recall",
                attachment_review=attachment_review,
            )
        if settings.runtime_profile == "full":
            return FullPostgresPreflightService(connection, settings).run(
                request, policy, attachment_review=attachment_review
            )
        response = PreflightService(connection, settings).run(
            request.model_copy(update={"attachment_review": attachment_review})
        )
        if response.attachment_uuid:
            session = connection.execute(
                "SELECT workspace_uuid FROM sessions WHERE session_uuid = ?",
                (request.session_uuid,),
            ).fetchone()
            workspace_uuid = request.workspace_uuid or (
                session["workspace_uuid"] if session else None
            )
            connection.execute(
                """
                UPDATE memory_context_attachments
                SET owner_user_uuid = ?, workspace_uuid = ?, lifecycle_status = 'prepared',
                    attachment_review = ?
                WHERE attachment_uuid = ?
                """,
                (request.user_uuid, workspace_uuid, attachment_review, response.attachment_uuid),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO memorist_attachment_lifecycle_events (
                    lifecycle_event_uuid, attachment_uuid, lifecycle_status, idempotency_key,
                    user_uuid, workspace_uuid, detail_ijson, created_at, schema_version
                ) VALUES (?, ?, 'prepared', ?, ?, ?, ?, CURRENT_TIMESTAMP, 1)
                """,
                (
                    new_uuid(),
                    response.attachment_uuid,
                    f"prepared:{response.attachment_uuid}",
                    request.user_uuid,
                    workspace_uuid,
                    dump_ijson({"attachment_review": attachment_review}),
                ),
            )
            attachment = connection.execute(
                "SELECT * FROM memory_context_attachments WHERE attachment_uuid = ?",
                (response.attachment_uuid,),
            ).fetchone()
            if attachment is not None:
                packet = json.loads(attachment["ijson_attachment"] or "{}")
                for provenance in packet.get("provenance", []):
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO memorist_retrieval_sources (
                            retrieval_source_uuid, retrieval_run_uuid, attachment_uuid,
                            source_type, source_uuid, memory_uuid, memory_version_uuid,
                            workspace_uuid, provenance_ijson, created_at, schema_version
                        ) VALUES (?, ?, ?, 'memory_version', ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, 1)
                        """,
                        (
                            new_uuid(),
                            response.retrieval_run_uuid,
                            response.attachment_uuid,
                            str(provenance["memory_version_uuid"]),
                            str(provenance["memory_uuid"]),
                            str(provenance["memory_version_uuid"]),
                            workspace_uuid,
                            dump_ijson({"canonical_store": "sqlite"}),
                        ),
                    )
                source_sets = (
                    ("active_block", attachment["source_block_uuids_ijson"]),
                    ("graph", attachment["source_fact_edge_uuids_ijson"]),
                )
                for source_type, encoded in source_sets:
                    for source_uuid in json.loads(encoded or "[]"):
                        connection.execute(
                            """
                            INSERT OR IGNORE INTO memorist_retrieval_sources (
                                retrieval_source_uuid, retrieval_run_uuid, attachment_uuid,
                                source_type, source_uuid, workspace_uuid, provenance_ijson,
                                created_at, schema_version
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, 1)
                            """,
                            (
                                new_uuid(),
                                response.retrieval_run_uuid,
                                response.attachment_uuid,
                                source_type,
                                str(source_uuid),
                                workspace_uuid,
                                dump_ijson({"canonical_store": "sqlite"}),
                            ),
                        )
            connection.commit()
        return response.model_copy(update={"attachment_review": attachment_review})


def _assistant_response_completed_full(
    settings: Settings,
    request: AssistantResponseCompletedRequest,
) -> dict[str, Any]:
    content_hash = sha256(request.assistant_text.encode("utf-8")).hexdigest()
    with memory_control_connection(settings) as connection:
        policy, contract = _policy_for_input(connection, settings, request)
        if policy.private:
            return {"skipped": True, "turn_policy": "private"}
        input_message = connection.execute(
            """
            SELECT m.*, s.workspace_uuid
            FROM messages m JOIN sessions s ON s.session_uuid = m.session_uuid
            WHERE m.message_uuid = ?
            """,
            (request.input_message_uuid,),
        ).fetchone()
        if input_message is None:
            raise HTTPException(status_code=404, detail="input message not found")
        actor_user = request.user_uuid or (
            str(contract.get("user_uuid")) if contract and contract.get("user_uuid") else None
        )
        actor_workspace = request.workspace_uuid or (
            str(contract.get("workspace_uuid"))
            if contract and contract.get("workspace_uuid")
            else str(input_message["workspace_uuid"])
        )
        control = MemoryControlRepository(connection, settings.runtime_profile)
        attachment_row: dict[str, Any] | None = None
        if request.attachment_uuid:
            if not policy.attachment_enabled:
                raise HTTPException(status_code=409, detail="turn policy forbids attachments")
            if not actor_user:
                raise HTTPException(status_code=401, detail="attachment actor identity required")
            attachment_row = control.attachment_for_actor(
                request.attachment_uuid,
                user_uuid=actor_user,
                workspace_uuid=actor_workspace,
            )
            if (
                attachment_row is None
                or attachment_row.get("input_message_uuid") != request.input_message_uuid
            ):
                raise HTTPException(status_code=404, detail="attachment not found")
            if attachment_row.get("lifecycle_status") != "delivered":
                raise HTTPException(status_code=409, detail="attachment was not delivered")
        existing = connection.execute(
            """
            SELECT * FROM assistant_response_links
            WHERE input_message_uuid = ?
              AND ((provider_response_id IS NOT NULL AND provider_response_id = ?)
                OR (? IS NULL AND content_hash = ?))
            """,
            (
                request.input_message_uuid,
                request.provider_response_id,
                request.provider_response_id,
                content_hash,
            ),
        ).fetchone()
        if existing is not None:
            return {
                "assistant_message_uuid": existing["assistant_message_uuid"],
                "response_link_uuid": existing["response_link_uuid"],
                "duplicate": True,
            }
        now = utc_now()
        next_turn = connection.execute(
            """
            SELECT COALESCE(MAX(turn_index), -1) + 1 AS next_turn
            FROM messages WHERE session_uuid = ?
            """,
            (input_message["session_uuid"],),
        ).fetchone()
        assistant_message_uuid = new_uuid()
        connection.execute(
            """
            INSERT INTO messages (
                message_uuid, session_uuid, turn_index, role, creator_type, raw_text,
                raw_payload_ijson, processing_status, visibility, is_deleted,
                created_at, updated_at, schema_version
            ) VALUES (?, ?, ?, 'assistant', 'memory_augmented_model', ?, ?, 'pending',
                      'visible', false, ?, ?, 1)
            """,
            (
                assistant_message_uuid,
                input_message["session_uuid"],
                int(next_turn["next_turn"]),
                request.assistant_text,
                dump_ijson(request.raw_payload) if request.raw_payload else None,
                now,
                now,
            ),
        )
        response_link_uuid = new_uuid()
        connection.execute(
            """
            INSERT INTO assistant_response_links (
                response_link_uuid, input_message_uuid, assistant_message_uuid,
                attachment_uuid, provider_response_id, content_hash, created_at, schema_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 1)
            """,
            (
                response_link_uuid,
                request.input_message_uuid,
                assistant_message_uuid,
                request.attachment_uuid,
                request.provider_response_id,
                content_hash,
                now,
            ),
        )
        for job_type, priority in (("text_unitization", 100), ("memory_extraction", 60)):
            payload = json.dumps(
                {
                    "message_uuid": assistant_message_uuid,
                    "session_uuid": input_message["session_uuid"],
                },
                sort_keys=True,
            )
            connection.execute(
                """
                INSERT INTO jobs (
                    job_uuid, job_type, priority, status, payload_jsonb, payload_ijson,
                    idempotency_key, run_after, attempts, max_attempts, created_at,
                    updated_at, schema_version
                ) VALUES (?, ?, ?, 'pending', ?::jsonb, ?, ?, ?, 0, 3, ?, ?, 1)
                ON CONFLICT (job_type, idempotency_key) DO NOTHING
                """,
                (
                    new_uuid(),
                    job_type,
                    priority,
                    payload,
                    payload,
                    f"openwebui:{job_type}:{assistant_message_uuid}",
                    now,
                    now,
                    now,
                ),
            )
        if request.attachment_uuid and attachment_row is not None and actor_user:
            control.transition_attachment(
                request.attachment_uuid,
                "used_for_response",
                user_uuid=actor_user,
                workspace_uuid=actor_workspace,
                idempotency_key=f"response:{response_link_uuid}",
                response_message_uuid=assistant_message_uuid,
            )
        if request.regeneration_uuid:
            connection.execute(
                """
                UPDATE memorist_regenerations
                SET regenerated_assistant_message_uuid = ?, status = 'completed', completed_at = ?
                WHERE regeneration_uuid = ? AND original_input_message_uuid = ?
                """,
                (
                    assistant_message_uuid,
                    now,
                    request.regeneration_uuid,
                    request.input_message_uuid,
                ),
            )
        connection.commit()
        control.audit(
            "assistant_capture",
            policy,
            session_uuid=str(input_message["session_uuid"]),
            input_message_uuid=request.input_message_uuid,
            workspace_uuid=actor_workspace,
            user_uuid=actor_user,
            attachment_uuid=request.attachment_uuid,
            detail={"regeneration": request.regeneration_uuid is not None},
        )
        return {
            "assistant_message_uuid": assistant_message_uuid,
            "response_link_uuid": response_link_uuid,
            "duplicate": False,
        }
