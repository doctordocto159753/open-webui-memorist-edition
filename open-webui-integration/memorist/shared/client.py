from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .config import MemoristIntegrationConfig, load_config
from .errors import MemoristCoreUnavailable, UnsafeMemoristUrl, sanitize_error
from .schemas import CapturedMessage, PreflightResult, ResolvedSession

_ALLOWED_HOSTS = {"localhost", "127.0.0.1", "::1", "memorist-core", "host.docker.internal"}
_SAFE_RETRY_METHODS = {"GET"}


class MemoristClient:
    def __init__(self, config: MemoristIntegrationConfig | None = None) -> None:
        self.config = config or load_config()
        self.base_url = _validate_local_url(self.config.core_url).rstrip("/")

    def health(self) -> dict[str, Any]:
        return self._request("GET", "/memcore/health", retry=True)

    def status(self) -> dict[str, Any]:
        return self._request("GET", "/memcore/openwebui/status", retry=True)

    def resolve_session(
        self,
        openwebui_conversation_id: str | None,
        title: str | None,
        user_id: str | None,
        temporary_chat_id: str | None = None,
        client_session_nonce: str | None = None,
        first_message_hash: str | None = None,
        created_at: str | None = None,
    ) -> ResolvedSession:
        data = self._request(
            "POST",
            "/memcore/openwebui/session/resolve",
            {
                "openwebui_conversation_id": openwebui_conversation_id,
                "temporary_chat_id": temporary_chat_id,
                "client_session_nonce": client_session_nonce,
                "first_message_hash": first_message_hash,
                "title": title,
                "user_id": user_id,
                "created_at": created_at,
            },
        )
        return ResolvedSession(
            session_uuid=str(data["session_uuid"]),
            workspace_uuid=data.get("workspace_uuid"),
            project_uuid=data.get("project_uuid"),
        )

    def capture_message(
        self,
        session_uuid: str,
        role: str,
        content: str,
        openwebui_conversation_id: str | None = None,
        temporary_chat_id: str | None = None,
        client_session_nonce: str | None = None,
        first_message_hash: str | None = None,
        openwebui_message_id: str | None = None,
        source_message_id: str | None = None,
        turn_index: int | None = None,
        timestamp: str | None = None,
        user_id: str | None = None,
        idempotency_key: str | None = None,
        raw_payload: dict[str, Any] | None = None,
    ) -> CapturedMessage:
        data = self._request(
            "POST",
            "/memcore/openwebui/messages/capture",
            {
                "session_uuid": session_uuid,
                "openwebui_conversation_id": openwebui_conversation_id,
                "temporary_chat_id": temporary_chat_id,
                "client_session_nonce": client_session_nonce,
                "first_message_hash": first_message_hash,
                "openwebui_message_id": openwebui_message_id,
                "source_message_id": source_message_id,
                "turn_index": turn_index,
                "timestamp": timestamp,
                "user_id": user_id,
                "role": role,
                "content": content,
                "idempotency_key": idempotency_key,
                "raw_payload": raw_payload,
            },
        )
        return CapturedMessage(
            session_uuid=str(data["session_uuid"]),
            message_uuid=str(data["message_uuid"]),
            duplicate=bool(data.get("duplicate", False)),
        )

    def preflight(
        self,
        session_uuid: str,
        input_message_uuid: str,
        target_model: str | None = None,
        model_provider: str | None = None,
        model_context_window: int | None = None,
        recent_conversation_text: str | None = None,
    ) -> PreflightResult:
        data = self._request(
            "POST",
            "/memcore/preflight",
            {
                "session_uuid": session_uuid,
                "input_message_uuid": input_message_uuid,
                "retrieval_mode": self.config.retrieval_mode,
                "token_budget": self.config.attachment_max_tokens,
                "target_model": target_model,
                "model_provider": model_provider,
                "model_context_window": model_context_window,
                "recent_conversation_text": recent_conversation_text,
            },
        )
        return PreflightResult(
            status=str(data.get("status", "unknown")),
            attachment_uuid=data.get("attachment_uuid"),
            retrieval_run_uuid=data.get("retrieval_run_uuid"),
            rendered_attachment=data.get("rendered_attachment"),
            token_count=int(data.get("token_count") or 0),
            effective_token_budget=int(data.get("effective_token_budget") or 0),
            budget_reason=data.get("budget_reason"),
            token_estimation_method=data.get("token_estimation_method"),
            attachment_mode=data.get("attachment_mode"),
        )

    def assistant_completed(
        self,
        input_message_uuid: str,
        assistant_text: str,
        attachment_uuid: str | None,
        provider_response_id: str | None,
        raw_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/memcore/assistant-response/completed",
            {
                "input_message_uuid": input_message_uuid,
                "assistant_text": assistant_text,
                "attachment_uuid": attachment_uuid,
                "provider_response_id": provider_response_id,
                "raw_payload": raw_payload,
            },
        )

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        retry: bool = False,
    ) -> dict[str, Any]:
        attempts = 2 if retry and method in _SAFE_RETRY_METHODS else 1
        last_error: BaseException | None = None
        for attempt in range(attempts):
            try:
                return self._send(method, path, payload)
            except (TimeoutError, urllib.error.URLError, OSError, json.JSONDecodeError) as error:
                last_error = error
                if attempt + 1 < attempts:
                    time.sleep(0.05)
        raise MemoristCoreUnavailable(sanitize_error(last_error or "request failed"))

    def _send(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None,
    ) -> dict[str, Any]:
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            self.base_url + path,
            data=data,
            headers={"Content-Type": "application/json"},
            method=method,
        )
        with urllib.request.urlopen(request, timeout=self.config.timeout_seconds) as response:
            body = response.read().decode("utf-8")
        decoded = json.loads(body) if body else {}
        if not isinstance(decoded, dict):
            raise MemoristCoreUnavailable("Memorist Core returned non-object response")
        return decoded


def _validate_local_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "http":
        raise UnsafeMemoristUrl("Memorist Core URL must use local http")
    host = parsed.hostname
    if host not in _ALLOWED_HOSTS:
        raise UnsafeMemoristUrl("Memorist Core URL must be local")
    if parsed.username or parsed.password:
        raise UnsafeMemoristUrl("Memorist Core URL must not contain credentials")
    return url
