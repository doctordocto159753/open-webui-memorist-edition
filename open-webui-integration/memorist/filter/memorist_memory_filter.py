from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from shared.client import MemoristClient  # noqa: E402
from shared.config import MemoristIntegrationConfig, load_config  # noqa: E402
from shared.errors import MemoristIntegrationError, sanitize_error  # noqa: E402
from shared.logging import warn  # noqa: E402
from shared.payload_parser import (  # noqa: E402
    insert_memory_attachment,
    parse_inlet_body,
    parse_outlet_body,
    response_key,
    safe_payload,
)


class Filter:
    class Valves:
        memorist_core_url: str = "http://localhost:8777"
        enabled: bool = True
        preflight_enabled: bool = True
        fail_open: bool = True
        debug: bool = False
        retrieval_mode: str = "standard"
        token_budget: int = 1800
        timeout_ms: int = 1200

    def __init__(self) -> None:
        self.valves = self.Valves()
        self._completed_response_keys: set[str] = set()

    def inlet(self, body: dict[str, Any], __user__: dict[str, Any] | None = None) -> dict[str, Any]:
        config = _config_from_valves(self.valves)
        if not config.enabled:
            return body
        parsed = parse_inlet_body(body, __user__)
        if parsed.user_message is None or parsed.content_text is None:
            return body
        metadata = parsed.metadata
        if parsed.warnings:
            metadata["memorist_parser_warnings"] = parsed.warnings
        if not parsed.user_id or not parsed.workspace_id:
            metadata["memorist_skipped_reason"] = "trusted_actor_identity_unavailable"
            return body
        try:
            client = MemoristClient(config)
            actor_user_id = parsed.user_id
            request_control = body.get("memorist")
            if request_control is not None and not isinstance(request_control, dict):
                raise MemoristIntegrationError("memorist request control must be an object")
            policy = client.resolve_turn_policy(
                user_id=actor_user_id,
                chat_id=parsed.conversation_id or parsed.temporary_chat_id,
                workspace_id=parsed.workspace_id,
                request_control=request_control,
            )
            metadata["memorist_turn_policy"] = policy.mode
            metadata["memorist_policy_source"] = policy.source
            metadata["memorist_runtime_profile"] = policy.runtime_profile
            metadata["memorist_attachment_review"] = policy.attachment_review
            metadata["memorist_recall_enabled"] = policy.recall_enabled
            metadata["openwebui_native_memory_independent"] = True
            if policy.private:
                metadata["memorist_private"] = True
                return body
            if policy.mode == "no_recall":
                # A stale preview/delivery marker must never cross a Send-without-Memorist
                # or regeneration boundary. The original capture may remain idempotently
                # linked, but the model request and assistant completion carry no attachment.
                metadata.pop("memorist_pending_attachment_uuid", None)
                metadata.pop("memorist_delivered_attachment_uuid", None)
                metadata.pop("memorist_attachment_uuid", None)
                metadata.pop("memorist_retrieval_run_uuid", None)
                metadata.pop("memorist_attachment_pending_review", None)
            if (
                policy.mode == "no_recall"
                and metadata.get("memorist_regeneration_uuid")
                and metadata.get("memorist_input_message_uuid")
            ):
                metadata["memorist_user_uuid"] = actor_user_id
                if parsed.workspace_id is not None:
                    metadata["memorist_workspace_uuid"] = parsed.workspace_id
                return body
            resolved = client.resolve_session(
                openwebui_conversation_id=parsed.conversation_id,
                title=parsed.title,
                user_id=actor_user_id,
                workspace_uuid=parsed.workspace_id,
                temporary_chat_id=parsed.temporary_chat_id,
                client_session_nonce=parsed.client_session_nonce,
                first_message_hash=parsed.first_message_hash,
                created_at=parsed.timestamp,
                turn_policy=policy.mode,
                attachment_review=policy.attachment_review,
            )
            review_capture_key = None
            if (
                metadata.get("memorist_review_ui_active")
                or metadata.get("memorist_approved_attachment_uuid")
                or metadata.get("memorist_review_disposition")
            ):
                if not parsed.message_id:
                    raise MemoristIntegrationError(
                        "attachment review requires the original Open WebUI message ID"
                    )
                review_capture_key = f"review-prepare:{actor_user_id}:{parsed.message_id}"
            captured = client.capture_message(
                resolved.session_uuid,
                "user",
                parsed.content_text,
                openwebui_conversation_id=parsed.conversation_id,
                temporary_chat_id=parsed.temporary_chat_id,
                client_session_nonce=parsed.client_session_nonce,
                first_message_hash=parsed.first_message_hash,
                openwebui_message_id=parsed.message_id,
                source_message_id=parsed.message_id,
                turn_index=parsed.turn_index,
                timestamp=parsed.timestamp,
                user_id=actor_user_id,
                raw_payload={"openwebui_message": safe_payload(parsed.user_message)},
                idempotency_key=review_capture_key,
                turn_policy=policy.mode,
                attachment_review=policy.attachment_review,
                workspace_uuid=resolved.workspace_uuid or parsed.workspace_id,
            )
            metadata["memorist_session_uuid"] = resolved.session_uuid
            metadata["memorist_input_message_uuid"] = captured.message_uuid
            metadata["memorist_user_uuid"] = actor_user_id
            metadata["memorist_workspace_uuid"] = resolved.workspace_uuid or parsed.workspace_id
            if metadata.get("memorist_review_disposition") in {
                "suppressed",
                "cancelled_before_send",
            }:
                # The preview already performed recall under the immutable Full policy.
                # Reuse its capture, but neither retrieve again nor deliver any context.
                metadata.pop("memorist_pending_attachment_uuid", None)
                metadata.pop("memorist_delivered_attachment_uuid", None)
                metadata.pop("memorist_attachment_uuid", None)
                metadata.pop("memorist_retrieval_run_uuid", None)
                metadata["memorist_attachment_pending_review"] = False
                return body
            approved_uuid = metadata.get("memorist_approved_attachment_uuid")
            if approved_uuid and policy.recall_enabled and policy.attachment_enabled:
                approved = client.preview_attachment(
                    str(approved_uuid),
                    user_id=actor_user_id,
                    workspace_uuid=str(resolved.workspace_uuid or parsed.workspace_id),
                )
                if (
                    approved.get("input_message_uuid") != captured.message_uuid
                    or approved.get("delivery_state") != "approved"
                    or not approved.get("rendered_attachment")
                ):
                    raise MemoristIntegrationError("approved attachment generation mismatch")
                client.record_attachment_delivery(
                    str(approved_uuid),
                    user_id=actor_user_id,
                    workspace_uuid=resolved.workspace_uuid or parsed.workspace_id,
                    idempotency_key=(
                        f"approved-filter-delivery:{captured.message_uuid}:{approved_uuid}:"
                        f"{approved.get('generation', 1)}"
                    ),
                )
                insert_memory_attachment(
                    body, str(approved["rendered_attachment"]), str(approved_uuid)
                )
                metadata["memorist_delivered_attachment_uuid"] = str(approved_uuid)
                metadata.pop("memorist_pending_attachment_uuid", None)
                metadata.pop("memorist_attachment_pending_review", None)
                return body
            if config.preflight_enabled and policy.recall_enabled and policy.attachment_enabled:
                self._attach_preflight(
                    body,
                    client,
                    resolved.session_uuid,
                    captured.message_uuid,
                    parsed.target_model,
                    parsed.model_provider,
                    parsed.model_context_window,
                    parsed.recent_conversation_text,
                    policy.mode,
                    actor_user_id,
                    resolved.workspace_uuid or parsed.workspace_id,
                    policy.attachment_review,
                )
        except Exception as error:
            return _handle_failure(body, config.fail_open, error)
        finally:
            parsed.user_message["content"] = parsed.original_content
        return body

    def outlet(
        self, body: dict[str, Any], __user__: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        config = _config_from_valves(self.valves)
        if not config.enabled:
            return body
        parsed = parse_outlet_body(body, __user__)
        metadata = parsed.metadata
        if parsed.warnings:
            metadata["memorist_parser_warnings"] = parsed.warnings
        input_message_uuid = metadata.get("memorist_input_message_uuid")
        assistant_text = parsed.assistant_text
        if not input_message_uuid or not assistant_text:
            return body
        actor_user = metadata.get("memorist_user_uuid") or (__user__ or {}).get("id")
        actor_workspace = metadata.get("memorist_workspace_uuid")
        if not actor_user or not actor_workspace:
            metadata["memorist_skipped_reason"] = "trusted_actor_identity_unavailable"
            return body
        current_response_key = response_key(body, assistant_text)
        if current_response_key in self._completed_response_keys:
            return body
        try:
            turn_policy = str(metadata.get("memorist_turn_policy") or "full")
            if turn_policy == "private":
                return body
            MemoristClient(config).assistant_completed(
                str(input_message_uuid),
                assistant_text,
                metadata.get("memorist_delivered_attachment_uuid"),
                parsed.provider_response_id,
                raw_payload={"openwebui_response_id": body.get("id")},
                turn_policy=turn_policy,
                user_id=str(actor_user),
                workspace_uuid=str(actor_workspace),
                regeneration_uuid=metadata.get("memorist_regeneration_uuid"),
            )
            self._completed_response_keys.add(current_response_key)
        except Exception as error:
            return _handle_failure(body, config.fail_open, error)
        return body

    def _attach_preflight(
        self,
        body: dict[str, Any],
        client: MemoristClient,
        session_uuid: str,
        input_message_uuid: str,
        target_model: str | None,
        model_provider: str | None,
        model_context_window: int | None,
        recent_conversation_text: str | None,
        turn_policy: str,
        user_id: str,
        workspace_uuid: str | None,
        attachment_review: bool,
    ) -> None:
        result = client.preflight(
            session_uuid,
            input_message_uuid,
            target_model=target_model,
            model_provider=model_provider,
            model_context_window=model_context_window,
            recent_conversation_text=recent_conversation_text,
            turn_policy=turn_policy,
            user_id=user_id,
            workspace_uuid=workspace_uuid,
            attachment_review=attachment_review,
        )
        if result.status != "attached" or not result.rendered_attachment:
            return
        metadata = _metadata(body)
        metadata["memorist_pending_attachment_uuid"] = result.attachment_uuid
        metadata["memorist_user_uuid"] = user_id
        metadata["memorist_workspace_uuid"] = workspace_uuid
        if result.attachment_review:
            metadata["memorist_attachment_pending_review"] = True
            review_ui_active = bool(metadata.get("memorist_review_ui_active"))
            if result.attachment_uuid and not review_ui_active:
                client.cancel_attachment_before_send(
                    result.attachment_uuid,
                    user_id=user_id,
                    workspace_uuid=workspace_uuid,
                    idempotency_key=f"review-no-ui:{input_message_uuid}:{result.attachment_uuid}",
                )
                metadata.pop("memorist_pending_attachment_uuid", None)
                metadata["memorist_attachment_pending_review"] = False
            return
        if result.attachment_uuid:
            client.record_attachment_delivery(
                result.attachment_uuid,
                user_id=user_id,
                workspace_uuid=workspace_uuid,
                idempotency_key=f"filter-delivery:{input_message_uuid}:{result.attachment_uuid}",
            )
        insert_memory_attachment(body, result.rendered_attachment, result.attachment_uuid)
        metadata["memorist_delivered_attachment_uuid"] = result.attachment_uuid
        metadata.pop("memorist_pending_attachment_uuid", None)
        metadata["memorist_retrieval_run_uuid"] = result.retrieval_run_uuid
        metadata["memorist_attachment_token_count"] = result.token_count
        metadata["memorist_effective_token_budget"] = result.effective_token_budget
        metadata["memorist_budget_reason"] = result.budget_reason


def _handle_failure(body: dict[str, Any], fail_open: bool, error: BaseException) -> dict[str, Any]:
    warn("Memorist integration skipped", error)
    metadata = _metadata(body)
    metadata["memorist_last_error"] = sanitize_error(error)
    if fail_open:
        return body
    metadata["memorist_failed_closed"] = True
    raise MemoristIntegrationError("Memorist preflight failed", developer_visible=True) from error


def _metadata(body: dict[str, Any]) -> dict[str, Any]:
    metadata = body.setdefault("metadata", {})
    if not isinstance(metadata, dict):
        metadata = {}
        body["metadata"] = metadata
    return metadata


def _config_from_valves(valves: Any) -> MemoristIntegrationConfig:
    base = load_config()
    return MemoristIntegrationConfig(
        core_url=str(getattr(valves, "memorist_core_url", base.core_url)),
        enabled=base.enabled and bool(getattr(valves, "enabled", base.enabled)),
        preflight_enabled=base.preflight_enabled
        and bool(getattr(valves, "preflight_enabled", base.preflight_enabled)),
        preflight_timeout_ms=int(getattr(valves, "timeout_ms", base.preflight_timeout_ms)),
        attachment_token_budget=int(getattr(valves, "token_budget", base.attachment_token_budget)),
        attachment_max_tokens=int(getattr(valves, "token_budget", base.attachment_max_tokens)),
        retrieval_mode=str(getattr(valves, "retrieval_mode", base.retrieval_mode)),
        fail_open=base.fail_open and bool(getattr(valves, "fail_open", base.fail_open)),
        debug=base.debug or bool(getattr(valves, "debug", base.debug)),
        actor_assertion_secret=base.actor_assertion_secret,
        actor_service_token=base.actor_service_token,
        actor_assertion_issuer=base.actor_assertion_issuer,
        actor_assertion_audience=base.actor_assertion_audience,
    )


def _safe_payload(value: dict[str, Any]) -> dict[str, Any]:
    return safe_payload(value)
