import sqlite3
from contextlib import suppress
from time import perf_counter

from pydantic import BaseModel, ConfigDict, Field

from memcore.attachments.budget import compute_attachment_budget
from memcore.attachments.builder import AttachmentBuilder
from memcore.config import Settings
from memcore.memory_worker.identity import execution_profile_fingerprint
from memcore.memory_worker.prompts.registry import (
    PromptExecutionRepository,
    PromptValidationError,
    render_prompt,
    validate_prompt_execution,
)
from memcore.model_control.registry import provider_for_profile
from memcore.model_control.repository import ModelControlRepository
from memcore.model_control.resolution import RoleResolutionService
from memcore.model_control.schemas import UsageEventCreate
from memcore.model_control.security import sanitize_error_message
from memcore.models import ModelRole, PreflightResponse, PreflightStatus
from memcore.repositories import RepositoryError
from memcore.repositories.retrieval import RetrievalRepository
from memcore.retrieval.runner import RetrievalRunner
from memcore.storage.write_actor import get_write_actor


class PreflightRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_uuid: str
    input_message_uuid: str
    retrieval_mode: str = "standard"
    token_budget: int | None = Field(default=None, ge=1)
    target_model_profile_uuid: str | None = None
    target_model: str | None = None
    model_provider: str | None = None
    model_context_window: int | None = Field(default=None, ge=1)
    recent_conversation_text: str | None = None
    estimated_recent_conversation_tokens: int | None = Field(default=None, ge=0)
    turn_policy: str | None = None
    user_uuid: str | None = None
    workspace_uuid: str | None = None
    attachment_review: bool = False


class PreflightService:
    def __init__(self, connection: sqlite3.Connection, settings: Settings) -> None:
        self.connection = connection
        self.settings = settings
        self.events = RetrievalRepository(connection)

    def run(self, request: PreflightRequest) -> PreflightResponse:
        started = perf_counter()
        if not self.settings.preflight_enabled:
            self.events.record_preflight_event(
                "attachment_skipped",
                {"reason": "preflight_disabled"},
                session_uuid=request.session_uuid,
                input_message_uuid=request.input_message_uuid,
            )
            return self._finish(
                request,
                PreflightResponse(
                    status=PreflightStatus.DISABLED,
                    latency_ms=_elapsed_ms(started),
                    attachment_mode="disabled",
                    budget_reason="preflight disabled",
                ),
            )

        try:
            writer_metrics = get_write_actor(self.settings.db_path).diagnostics()
            if int(writer_metrics["queue_depth"]) >= self.settings.import_max_write_queue_depth:
                self.events.record_preflight_event(
                    "attachment_skipped",
                    {
                        "reason": "write_queue_backpressure",
                        "write_queue_depth": writer_metrics["queue_depth"],
                    },
                    session_uuid=request.session_uuid,
                    input_message_uuid=request.input_message_uuid,
                )
                return self._finish(
                    request,
                    PreflightResponse(
                        status=PreflightStatus.SKIPPED_BACKPRESSURE,
                        abstention_status="skipped_backpressure",
                        latency_ms=_elapsed_ms(started),
                        attachment_mode="disabled",
                        budget_reason="write actor queue depth exceeded preflight threshold",
                    ),
                )
            budget = compute_attachment_budget(
                mode=request.retrieval_mode,
                requested_budget=request.token_budget,
                target_model=request.target_model,
                model_context_window=request.model_context_window,
                recent_conversation_text=request.recent_conversation_text,
                estimated_recent_conversation_tokens=request.estimated_recent_conversation_tokens,
                max_tokens=self.settings.attachment_max_tokens,
                context_ratio=self.settings.attachment_context_ratio,
                min_tokens=self.settings.attachment_min_tokens,
                reserved_completion_tokens=self.settings.attachment_reserved_completion_tokens,
                fallback_context_window=self.settings.unknown_model_context_window,
                safety_margin_tokens=self.settings.attachment_safety_margin_tokens,
            )
            if budget.effective_token_budget <= 0:
                self.events.record_preflight_event(
                    "attachment_skipped",
                    {
                        "reason": "dynamic_budget_disabled",
                        "budget_reason": budget.budget_reason,
                        "warnings": budget.warnings,
                    },
                    session_uuid=request.session_uuid,
                    input_message_uuid=request.input_message_uuid,
                )
                return self._finish(
                    request,
                    PreflightResponse(
                        status=PreflightStatus.ABSTAINED,
                        abstention_status="budget_exhausted",
                        latency_ms=_elapsed_ms(started),
                        effective_token_budget=0,
                        budget_reason=budget.budget_reason,
                        token_estimation_method=budget.token_estimation_method,
                        attachment_mode=budget.attachment_mode,
                        budget_warnings=budget.warnings,
                    ),
                )
            self.events.record_preflight_event(
                "retrieval_started",
                {
                    "mode": request.retrieval_mode,
                    "effective_token_budget": budget.effective_token_budget,
                    "budget_reason": budget.budget_reason,
                },
                session_uuid=request.session_uuid,
                input_message_uuid=request.input_message_uuid,
            )
            run, _plan, selection = RetrievalRunner(self.connection, self.settings).run(
                request.session_uuid,
                request.input_message_uuid,
                request.retrieval_mode,
                budget.effective_token_budget,
            )
            self.events.record_preflight_event(
                "retrieval_completed",
                {
                    "selected_memory_count": len(selection.selected),
                    "abstention_reason": selection.abstention_reason,
                },
                session_uuid=request.session_uuid,
                input_message_uuid=request.input_message_uuid,
                retrieval_run_uuid=run.retrieval_run_uuid,
            )
            self._run_preflight_model(
                request,
                run.retrieval_run_uuid,
                budget.effective_token_budget,
            )
            if _elapsed_ms(started) > self.settings.preflight_timeout_ms:
                self.events.record_preflight_event(
                    "attachment_skipped",
                    {"reason": "timeout"},
                    session_uuid=request.session_uuid,
                    input_message_uuid=request.input_message_uuid,
                    retrieval_run_uuid=run.retrieval_run_uuid,
                )
                return self._finish(
                    request,
                    PreflightResponse(
                        status=PreflightStatus.TIMEOUT,
                        retrieval_run_uuid=run.retrieval_run_uuid,
                        abstention_status="timeout",
                        latency_ms=_elapsed_ms(started),
                        effective_token_budget=budget.effective_token_budget,
                        budget_reason=budget.budget_reason,
                        token_estimation_method=budget.token_estimation_method,
                        attachment_mode=budget.attachment_mode,
                        budget_warnings=budget.warnings,
                    ),
                )

            if not selection.selected:
                self.events.record_preflight_event(
                    "retrieval_abstained",
                    {"reason": selection.abstention_reason},
                    session_uuid=request.session_uuid,
                    input_message_uuid=request.input_message_uuid,
                    retrieval_run_uuid=run.retrieval_run_uuid,
                )
                return self._finish(
                    request,
                    PreflightResponse(
                        status=PreflightStatus.ABSTAINED,
                        retrieval_run_uuid=run.retrieval_run_uuid,
                        abstention_status=selection.abstention_status,
                        latency_ms=_elapsed_ms(started),
                        effective_token_budget=budget.effective_token_budget,
                        budget_reason=budget.budget_reason,
                        token_estimation_method=budget.token_estimation_method,
                        attachment_mode=budget.attachment_mode,
                        budget_warnings=budget.warnings,
                    ),
                )

            attachment_uuid, rendered, token_count = AttachmentBuilder(self.connection).build(
                retrieval_run_uuid=run.retrieval_run_uuid,
                session_uuid=request.session_uuid,
                input_message_uuid=request.input_message_uuid,
                mode=budget.attachment_mode,
                selected_items=selection.selected,
                token_budget=budget.effective_token_budget,
                abstention_status=selection.abstention_status,
                abstention_reason=selection.abstention_reason,
            )
            self.events.record_preflight_event(
                "attachment_built",
                {"token_count": token_count},
                session_uuid=request.session_uuid,
                input_message_uuid=request.input_message_uuid,
                attachment_uuid=attachment_uuid,
                retrieval_run_uuid=run.retrieval_run_uuid,
            )
            self.events.record_preflight_event(
                "attachment_prepared",
                {"token_count": token_count},
                session_uuid=request.session_uuid,
                input_message_uuid=request.input_message_uuid,
                attachment_uuid=attachment_uuid,
                retrieval_run_uuid=run.retrieval_run_uuid,
            )
            return self._finish(
                request,
                PreflightResponse(
                    status=PreflightStatus.ATTACHED,
                    attachment_uuid=attachment_uuid,
                    retrieval_run_uuid=run.retrieval_run_uuid,
                    rendered_attachment=rendered,
                    token_count=token_count,
                    abstention_status=selection.abstention_status,
                    latency_ms=_elapsed_ms(started),
                    effective_token_budget=budget.effective_token_budget,
                    budget_reason=budget.budget_reason,
                    token_estimation_method=budget.token_estimation_method,
                    attachment_mode=budget.attachment_mode,
                    budget_warnings=budget.warnings,
                    attachment_review=request.attachment_review,
                ),
            )
        except Exception as error:
            self.events.record_preflight_event(
                "retrieval_failed",
                {"error": type(error).__name__},
                session_uuid=request.session_uuid,
                input_message_uuid=request.input_message_uuid,
            )
            if self.settings.preflight_fail_open:
                return self._finish(
                    request,
                    PreflightResponse(
                        status=PreflightStatus.FAILED_OPEN,
                        abstention_status="failed_open",
                        latency_ms=_elapsed_ms(started),
                        attachment_mode="disabled",
                        budget_reason="preflight failed open",
                    ),
                    error_class=type(error).__name__,
                )
            raise RepositoryError("preflight failed") from error

    def _run_preflight_model(
        self,
        request: PreflightRequest,
        retrieval_run_uuid: str,
        effective_token_budget: int,
    ) -> None:
        repository = ModelControlRepository(self.connection)
        scope = self.connection.execute(
            "SELECT workspace_uuid, project_uuid FROM sessions WHERE session_uuid = ?",
            (request.session_uuid,),
        ).fetchone()
        resolution = RoleResolutionService(repository).resolve(
            ModelRole.PREFLIGHT,
            workspace_uuid=(
                str(scope["workspace_uuid"]) if scope and scope["workspace_uuid"] else None
            ),
            project_uuid=(str(scope["project_uuid"]) if scope and scope["project_uuid"] else None),
        )
        profile = (
            repository.get_profile(resolution.model_profile_uuid)
            if resolution.model_profile_uuid is not None
            else None
        )
        profile_uuid = profile.model_profile_uuid if profile else None
        provider = provider_for_profile(profile or resolution.runtime_profile())
        execution_profile = (
            profile.model_dump(mode="json") if profile else resolution.runtime_profile()
        )
        expected_profile_fingerprint = execution_profile_fingerprint(execution_profile)
        self.events.record_preflight_event(
            "preflight_model_started",
            {
                "model_profile_uuid": profile_uuid,
                "provider_type": provider.provider_type,
                "model_name": provider.model_name,
                "timeout_ms": self.settings.preflight_model_timeout_ms,
            },
            session_uuid=request.session_uuid,
            input_message_uuid=request.input_message_uuid,
            retrieval_run_uuid=retrieval_run_uuid,
        )
        started = perf_counter()
        input_payload = _preflight_input_payload(request, effective_token_budget)
        raw_output: dict[str, object] | None = None
        try:
            if provider.provider_type == "disabled":
                output = _valid_preflight_planning_output("abstain", effective_token_budget)
                status = "disabled"
                input_tokens = 0
                output_tokens = 0
            elif provider.provider_type in {"deterministic", "local_embedding"}:
                output = _valid_preflight_planning_output("ok", effective_token_budget)
                status = "ok"
                input_tokens = request.estimated_recent_conversation_tokens or 0
                output_tokens = 16
            else:
                self.connection.commit()
                response = provider.complete_structured(
                    _preflight_prompt(input_payload),
                    timeout_seconds=self.settings.preflight_model_timeout_ms / 1000,
                )
                output = response.output_ijson
                input_tokens = response.input_tokens
                output_tokens = response.output_tokens
                status = "ok"
                current_resolution = RoleResolutionService(repository).resolve(
                    ModelRole.PREFLIGHT,
                    workspace_uuid=(
                        str(scope["workspace_uuid"]) if scope and scope["workspace_uuid"] else None
                    ),
                    project_uuid=(
                        str(scope["project_uuid"]) if scope and scope["project_uuid"] else None
                    ),
                )
                current_profile = (
                    repository.get_profile(current_resolution.model_profile_uuid)
                    if current_resolution.model_profile_uuid
                    else None
                )
                current_values = (
                    current_profile.model_dump(mode="json")
                    if current_profile
                    else current_resolution.runtime_profile()
                )
                if execution_profile_fingerprint(current_values) != expected_profile_fingerprint:
                    raise RuntimeError("preflight processing profile changed during provider call")
            raw_output = dict(output)
            validate_prompt_execution(
                "memorist.preflight_planning",
                "2.0",
                input_payload,
                dict(output),
            )
            latency_ms = _elapsed_ms(started)
            PromptExecutionRepository(self.connection).record_execution(
                prompt_id="memorist.preflight_planning",
                prompt_version="2.0",
                model_role=ModelRole.PREFLIGHT,
                provider_type=provider.provider_type,
                model_name=provider.model_name,
                model_profile_uuid=profile_uuid,
                session_uuid=request.session_uuid,
                message_uuid=request.input_message_uuid,
                input_value=input_payload,
                input_ref=f"preflight:{retrieval_run_uuid}",
                raw_output_value=dict(output),
                validated_output_value=dict(output),
                status=status,
                warnings=_output_warnings(output),
                latency_ms=latency_ms,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
            repository.record_usage_event(
                UsageEventCreate(
                    role=ModelRole.PREFLIGHT,
                    stage="preflight_model",
                    model_profile_uuid=profile_uuid,
                    session_uuid=request.session_uuid,
                    message_uuid=request.input_message_uuid,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    latency_ms=latency_ms,
                    status=status,
                )
            )
            self.events.record_preflight_event(
                "preflight_model_completed",
                {
                    "model_profile_uuid": profile_uuid,
                    "provider_type": provider.provider_type,
                    "status": status,
                    "latency_ms": latency_ms,
                },
                session_uuid=request.session_uuid,
                input_message_uuid=request.input_message_uuid,
                retrieval_run_uuid=retrieval_run_uuid,
            )
        except (PromptValidationError, TimeoutError, OSError, RuntimeError, ValueError) as error:
            latency_ms = _elapsed_ms(started)
            with suppress(Exception):
                PromptExecutionRepository(self.connection).record_execution(
                    prompt_id="memorist.preflight_planning",
                    prompt_version="2.0",
                    model_role=ModelRole.PREFLIGHT,
                    provider_type=provider.provider_type,
                    model_name=provider.model_name,
                    model_profile_uuid=profile_uuid,
                    session_uuid=request.session_uuid,
                    message_uuid=request.input_message_uuid,
                    input_value=input_payload,
                    input_ref=f"preflight:{retrieval_run_uuid}",
                    raw_output_value=raw_output,
                    validated_output_value=None,
                    status="error",
                    warnings=[type(error).__name__],
                    error_sanitized=str(error),
                    latency_ms=latency_ms,
                    input_tokens=request.estimated_recent_conversation_tokens or 0,
                )
            repository.record_usage_event(
                UsageEventCreate(
                    role=ModelRole.PREFLIGHT,
                    stage="preflight_model",
                    model_profile_uuid=profile_uuid,
                    session_uuid=request.session_uuid,
                    message_uuid=request.input_message_uuid,
                    input_tokens=request.estimated_recent_conversation_tokens or 0,
                    latency_ms=latency_ms,
                    status="failed_open" if self.settings.preflight_fail_open else "failed",
                    error_class=type(error).__name__,
                    error_message_sanitized=sanitize_error_message(str(error)),
                )
            )
            event_type = (
                "preflight_model_failed_open"
                if self.settings.preflight_fail_open
                else "preflight_model_failed"
            )
            self.events.record_preflight_event(
                event_type,
                {
                    "model_profile_uuid": profile_uuid,
                    "provider_type": provider.provider_type,
                    "error_class": type(error).__name__,
                },
                session_uuid=request.session_uuid,
                input_message_uuid=request.input_message_uuid,
                retrieval_run_uuid=retrieval_run_uuid,
            )
            if not self.settings.preflight_fail_open:
                raise

    def _finish(
        self,
        request: PreflightRequest,
        response: PreflightResponse,
        error_class: str | None = None,
    ) -> PreflightResponse:
        try:
            repository = ModelControlRepository(self.connection)
            scope = self.connection.execute(
                "SELECT workspace_uuid, project_uuid FROM sessions WHERE session_uuid = ?",
                (request.session_uuid,),
            ).fetchone()
            resolution = RoleResolutionService(repository).resolve(
                ModelRole.PREFLIGHT,
                workspace_uuid=(
                    str(scope["workspace_uuid"]) if scope and scope["workspace_uuid"] else None
                ),
                project_uuid=(
                    str(scope["project_uuid"]) if scope and scope["project_uuid"] else None
                ),
            )
            model_profile_uuid = resolution.model_profile_uuid
            repository.record_usage_event(
                UsageEventCreate(
                    role=ModelRole.PREFLIGHT,
                    stage="preflight",
                    model_profile_uuid=model_profile_uuid,
                    session_uuid=request.session_uuid,
                    message_uuid=request.input_message_uuid,
                    attachment_uuid=response.attachment_uuid,
                    input_tokens=request.estimated_recent_conversation_tokens or 0,
                    output_tokens=response.token_count,
                    latency_ms=response.latency_ms,
                    status=response.status.value,
                    error_class=error_class,
                )
            )
        except Exception:
            pass
        return response


def _elapsed_ms(started: float) -> int:
    return int((perf_counter() - started) * 1000)


def _output_warnings(output: dict[str, object]) -> list[str]:
    warnings = output.get("warnings")
    if not isinstance(warnings, list):
        return []
    return [str(item) for item in warnings]


def _valid_preflight_planning_output(status: str, token_budget: int) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "prompt_id": "memorist.preflight_planning",
        "prompt_version": "2.0",
        "status": status,
        "warnings": [] if status == "ok" else ["preflight provider disabled"],
        "items": [
            {
                "attachment_mode": "standard" if token_budget > 0 else "disabled",
                "selected_memory_ids": [],
                "excluded_memory_ids": [],
                "trusted_directive_ids": [],
                "ordinary_memory_ids": [],
                "conflict_ids": [],
                "compression_strategy": "source_linked_brief" if token_budget > 0 else "none",
                "abstain_reason": None if status == "ok" else "preflight provider disabled",
                "security_notes": [],
                "estimated_tokens": max(0, token_budget),
            }
        ],
    }


def _preflight_input_payload(request: PreflightRequest, token_budget: int) -> dict[str, object]:
    return {
        "current_user_message": request.recent_conversation_text or "",
        "main_chat_model": {
            "provider": request.model_provider or "openwebui",
            "model_name": request.target_model or "openwebui-selected",
            "context_window": request.model_context_window or 0,
        },
        "attachment_budget": {
            "max_tokens": token_budget,
            "mode": request.retrieval_mode
            if request.retrieval_mode in {"lite", "standard", "full"}
            else "standard",
        },
        "active_blocks": [],
        "retrieval_candidates": [],
        "conflicts": [],
        "security_warnings": [],
    }


def _preflight_prompt(input_payload: dict[str, object]) -> str:
    return render_prompt("memorist.preflight_planning", "2.0", {"PAYLOAD_IJSON": input_payload})
