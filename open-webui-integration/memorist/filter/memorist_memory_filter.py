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
    content_text,
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
        try:
            client = MemoristClient(config)
            resolved = client.resolve_session(
                openwebui_conversation_id=parsed.conversation_id,
                title=parsed.title,
                user_id=parsed.user_id,
                temporary_chat_id=parsed.temporary_chat_id,
                client_session_nonce=parsed.client_session_nonce,
                first_message_hash=parsed.first_message_hash,
                created_at=parsed.timestamp,
            )
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
                user_id=parsed.user_id,
                raw_payload={"openwebui_message": safe_payload(parsed.user_message)},
            )
            metadata["memorist_session_uuid"] = resolved.session_uuid
            metadata["memorist_input_message_uuid"] = captured.message_uuid
            if config.preflight_enabled:
                self._attach_preflight(
                    body,
                    client,
                    resolved.session_uuid,
                    captured.message_uuid,
                    parsed.target_model,
                    parsed.model_provider,
                    parsed.model_context_window,
                    parsed.recent_conversation_text,
                )
        except Exception as error:
            return _handle_failure(body, config.fail_open, error)
        finally:
            parsed.user_message["content"] = parsed.original_content
        return body

    def outlet(self, body: dict[str, Any], __user__: dict[str, Any] | None = None) -> dict[str, Any]:
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
        current_response_key = response_key(body, assistant_text)
        if current_response_key in self._completed_response_keys:
            return body
        try:
            MemoristClient(config).assistant_completed(
                str(input_message_uuid),
                assistant_text,
                metadata.get("memorist_attachment_uuid"),
                parsed.provider_response_id,
                raw_payload={"openwebui_response_id": body.get("id")},
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
    ) -> None:
        result = client.preflight(
            session_uuid,
            input_message_uuid,
            target_model=target_model,
            model_provider=model_provider,
            model_context_window=model_context_window,
            recent_conversation_text=recent_conversation_text,
        )
        if result.status != "attached" or not result.rendered_attachment:
            return
        insert_memory_attachment(body, result.rendered_attachment, result.attachment_uuid)
        metadata = _metadata(body)
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
    )


def _safe_payload(value: dict[str, Any]) -> dict[str, Any]:
    return safe_payload(value)
