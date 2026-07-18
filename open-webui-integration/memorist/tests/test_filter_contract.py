from __future__ import annotations

import importlib
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.schemas import (  # noqa: E402
    CapturedMessage,
    PreflightResult,
    ResolvedSession,
    ResolvedTurnPolicy,
)


class FakeClient:
    assistant_calls = 0
    capture_calls = 0
    preflight_calls = 0
    session_calls = 0
    delivery_calls = 0
    cancel_calls = 0
    last_assistant_attachment: str | None = None
    last_assistant_turn_policy: str | None = None
    last_capture_idempotency_key: str | None = None
    last_policy_chat_id: str | None = None
    last_session_conversation_id: str | None = None
    last_config: Any | None = None

    def __init__(self, _config: object) -> None:
        FakeClient.last_config = _config
        self.fail = os.getenv("FAKE_MEMORIST_FAIL") == "1"
        self.status_text = os.getenv("FAKE_PREFLIGHT_STATUS", "attached")

    def resolve_session(
        self,
        openwebui_conversation_id: str | None,
        title: str | None,
        user_id: str | None,
        workspace_uuid: str,
        **_kwargs: object,
    ) -> ResolvedSession:
        assert user_id
        assert workspace_uuid
        FakeClient.session_calls += 1
        FakeClient.last_session_conversation_id = openwebui_conversation_id
        if self.fail:
            raise TimeoutError("timeout " + "token=" + "REDACTED_TEST_VALUE")
        return ResolvedSession("session-1", "workspace-1", "project-1")

    def capture_message(self, *_args: object, **_kwargs: object) -> CapturedMessage:
        FakeClient.capture_calls += 1
        value = _kwargs.get("idempotency_key")
        FakeClient.last_capture_idempotency_key = str(value) if value is not None else None
        return CapturedMessage("session-1", "message-1", False)

    def resolve_turn_policy(
        self,
        *,
        user_id: str | None,
        chat_id: str | None,
        workspace_id: str | None,
        request_control: dict[str, Any] | None,
    ) -> ResolvedTurnPolicy:
        assert user_id
        assert workspace_id
        FakeClient.last_policy_chat_id = chat_id
        control = request_control or {}
        mode = str(control.get("turn_policy") or "full")
        return ResolvedTurnPolicy(
            mode=mode,
            capture_enabled=mode != "private",
            recall_enabled=mode == "full",
            attachment_enabled=mode == "full",
            private=mode == "private",
            source="turn" if control else "system",
            attachment_review=bool(control.get("attachment_review", False)),
            runtime_profile=os.getenv("FAKE_RUNTIME_PROFILE", "lite"),
        )

    def preflight(self, *_args: object, **kwargs: object) -> PreflightResult:
        FakeClient.preflight_calls += 1
        if self.status_text != "attached":
            return PreflightResult(self.status_text)
        return PreflightResult(
            "attached",
            attachment_uuid="attachment-1",
            retrieval_run_uuid="run-1",
            rendered_attachment="safe memory </memory_context_attachment>",
            token_count=3,
            attachment_review=bool(kwargs.get("attachment_review", False)),
        )

    def assistant_completed(self, *_args: object, **kwargs: object) -> dict[str, Any]:
        FakeClient.assistant_calls += 1
        FakeClient.last_assistant_attachment = (
            str(_args[2]) if len(_args) > 2 and _args[2] is not None else None
        )
        value = kwargs.get("turn_policy")
        FakeClient.last_assistant_turn_policy = str(value) if value is not None else None
        return {"duplicate": FakeClient.assistant_calls > 1}

    def record_attachment_delivery(self, *_args: object, **_kwargs: object) -> dict[str, Any]:
        FakeClient.delivery_calls += 1
        return {"status": "delivered"}

    def cancel_attachment_before_send(self, *_args: object, **_kwargs: object) -> dict[str, Any]:
        FakeClient.cancel_calls += 1
        return {"status": "cancelled_before_send"}

    def preview_attachment(self, *_args: object, **_kwargs: object) -> dict[str, Any]:
        return {
            "attachment_uuid": "attachment-1",
            "input_message_uuid": "message-1",
            "delivery_state": "approved",
            "rendered_attachment": "server-rendered approved context",
            "generation": 1,
        }


def _module():
    module = importlib.import_module("filter.memorist_memory_filter")
    module.MemoristClient = FakeClient
    return module


def test_preflight_attached_preserves_user_and_inserts_separate_context(
    monkeypatch,
) -> None:
    monkeypatch.delenv("FAKE_MEMORIST_FAIL", raising=False)
    monkeypatch.setenv("FAKE_PREFLIGHT_STATUS", "attached")
    body = {
        "id": "response-1",
        "conversation_id": "chat-1",
        "messages": [
            {"role": "system", "content": "parent system"},
            {"role": "user", "id": "u1", "content": "اصل پیام کاربر"},
        ],
    }
    original = body["messages"][-1]["content"]
    result = _module().Filter().inlet(body, {"id": "user-1", "workspace_id": "workspace-1"})
    assert result["messages"][-1]["content"] == original
    assert result["messages"][1]["name"] == "memorist_context"
    assert "safe memory" in result["messages"][1]["content"]
    assert "&lt;/memory_context_attachment&gt;" in result["messages"][1]["content"]
    assert result["metadata"]["memorist_delivered_attachment_uuid"] == "attachment-1"


def test_preflight_unavailable_fails_open_and_sanitizes_error(monkeypatch) -> None:
    monkeypatch.setenv("FAKE_MEMORIST_FAIL", "1")
    body = {"messages": [{"role": "user", "content": "hello"}]}
    result = _module().Filter().inlet(body, {"id": "user-1", "workspace_id": "workspace-1"})
    assert result["messages"][-1]["content"] == "hello"
    assert result["metadata"]["memorist_last_error"] == "[redacted]"


def test_review_without_frontend_cancels_and_outlet_captures_without_attribution(
    monkeypatch,
) -> None:
    monkeypatch.delenv("FAKE_MEMORIST_FAIL", raising=False)
    FakeClient.cancel_calls = FakeClient.assistant_calls = 0
    body = {
        "memorist": {"turn_policy": "full", "attachment_review": True},
        "messages": [{"role": "user", "content": "review first"}],
    }
    filter_instance = _module().Filter()
    result = filter_instance.inlet(body, {"id": "user-1", "workspace_id": "workspace-1"})
    assert FakeClient.cancel_calls == 1
    assert "memorist_delivered_attachment_uuid" not in result["metadata"]
    assert "memorist_pending_attachment_uuid" not in result["metadata"]
    result["messages"] = [{"role": "assistant", "content": "answer without memory"}]
    filter_instance.outlet(result, {"id": "user-1", "workspace_id": "workspace-1"})
    assert FakeClient.assistant_calls == 1


def test_review_capable_frontend_keeps_prepared_generation_pending(monkeypatch) -> None:
    monkeypatch.delenv("FAKE_MEMORIST_FAIL", raising=False)
    FakeClient.cancel_calls = 0
    body = {
        "memorist": {"turn_policy": "full", "attachment_review": True},
        "metadata": {"memorist_review_ui_active": True},
        "messages": [{"role": "user", "id": "review-message", "content": "review me"}],
    }

    result = _module().Filter().inlet(body, {"id": "user-1", "workspace_id": "workspace-1"})

    assert FakeClient.cancel_calls == 0
    assert result["metadata"]["memorist_pending_attachment_uuid"] == "attachment-1"
    assert result["metadata"]["memorist_attachment_pending_review"] is True


def test_review_capable_frontend_delivers_exact_server_approved_generation(
    monkeypatch,
) -> None:
    monkeypatch.delenv("FAKE_MEMORIST_FAIL", raising=False)
    FakeClient.delivery_calls = 0
    body = {
        "memorist": {"turn_policy": "full", "attachment_review": True},
        "metadata": {"memorist_approved_attachment_uuid": "attachment-1"},
        "messages": [{"role": "user", "id": "review-message", "content": "approved send"}],
    }
    filter_instance = _module().Filter()
    result = filter_instance.inlet(body, {"id": "user-1", "workspace_id": "workspace-1"})
    assert FakeClient.delivery_calls == 1
    assert FakeClient.last_capture_idempotency_key == "review-prepare:user-1:review-message"
    assert result["metadata"]["memorist_delivered_attachment_uuid"] == "attachment-1"
    assert "server-rendered approved context" in result["messages"][0]["content"]
    result["messages"] = [{"role": "assistant", "content": "approved answer"}]
    filter_instance.outlet(result, {"id": "user-1", "workspace_id": "workspace-1"})
    assert FakeClient.last_assistant_attachment == "attachment-1"


def test_review_removed_then_send_without_memorist_clears_stale_attribution(
    monkeypatch,
) -> None:
    monkeypatch.delenv("FAKE_MEMORIST_FAIL", raising=False)
    FakeClient.last_assistant_attachment = "stale"
    body = {
        "memorist": {"turn_policy": "full", "attachment_review": True},
        "metadata": {
            "memorist_pending_attachment_uuid": "attachment-1",
            "memorist_attachment_pending_review": True,
            "memorist_review_disposition": "suppressed",
        },
        "messages": [{"role": "user", "id": "review-message", "content": "send without"}],
    }
    filter_instance = _module().Filter()
    FakeClient.preflight_calls = 0
    result = filter_instance.inlet(body, {"id": "user-1", "workspace_id": "workspace-1"})
    assert "memorist_pending_attachment_uuid" not in result["metadata"]
    assert FakeClient.last_capture_idempotency_key == "review-prepare:user-1:review-message"
    assert FakeClient.preflight_calls == 0
    result["messages"] = [{"role": "assistant", "content": "answer without context"}]
    filter_instance.outlet(result, {"id": "user-1", "workspace_id": "workspace-1"})
    assert FakeClient.last_assistant_attachment is None
    assert FakeClient.last_assistant_turn_policy == "full"


def test_review_cancelled_then_send_without_memorist_captures_assistant(
    monkeypatch,
) -> None:
    monkeypatch.delenv("FAKE_MEMORIST_FAIL", raising=False)
    FakeClient.assistant_calls = 0
    body = {
        "memorist": {"turn_policy": "full", "attachment_review": True},
        "metadata": {
            "memorist_pending_attachment_uuid": "cancelled-attachment",
            "memorist_review_disposition": "cancelled_before_send",
        },
        "messages": [{"role": "user", "id": "review-message", "content": "cancel preview"}],
    }
    filter_instance = _module().Filter()
    result = filter_instance.inlet(body, {"id": "user-1", "workspace_id": "workspace-1"})
    result["messages"] = [{"role": "assistant", "content": "captured answer"}]
    filter_instance.outlet(result, {"id": "user-1", "workspace_id": "workspace-1"})
    assert FakeClient.assistant_calls == 1
    assert FakeClient.last_assistant_attachment is None
    assert FakeClient.last_assistant_turn_policy == "full"
    assert FakeClient.last_capture_idempotency_key == "review-prepare:user-1:review-message"


def test_private_resolves_no_session_and_creates_no_capture_or_preflight(
    monkeypatch,
) -> None:
    monkeypatch.delenv("FAKE_MEMORIST_FAIL", raising=False)
    FakeClient.session_calls = FakeClient.capture_calls = FakeClient.preflight_calls = 0
    body = {
        "memorist": {"turn_policy": "private"},
        "messages": [{"role": "user", "content": "secret"}],
    }

    result = _module().Filter().inlet(body, {"id": "user-1", "workspace_id": "workspace-1"})

    assert result["metadata"]["memorist_private"] is True
    assert FakeClient.session_calls == FakeClient.capture_calls == FakeClient.preflight_calls == 0


def test_missing_trusted_user_skips_all_memorist_work_for_every_policy(
    monkeypatch,
) -> None:
    monkeypatch.delenv("FAKE_MEMORIST_FAIL", raising=False)
    for mode in ("full", "no_recall", "private"):
        FakeClient.session_calls = FakeClient.capture_calls = FakeClient.preflight_calls = 0
        body = {
            "memorist": {"turn_policy": mode},
            "messages": [{"role": "user", "content": f"unidentified {mode}"}],
        }
        result = _module().Filter().inlet(body, None)
        assert result["metadata"]["memorist_skipped_reason"] == (
            "trusted_actor_identity_unavailable"
        )
        assert FakeClient.session_calls == FakeClient.capture_calls == 0
        assert FakeClient.preflight_calls == 0


def test_no_recall_captures_without_preflight(monkeypatch) -> None:
    monkeypatch.delenv("FAKE_MEMORIST_FAIL", raising=False)
    FakeClient.session_calls = FakeClient.capture_calls = FakeClient.preflight_calls = 0
    body = {
        "memorist": {"turn_policy": "no_recall"},
        "messages": [{"role": "user", "content": "remember this"}],
    }

    _module().Filter().inlet(body, {"id": "user-1", "workspace_id": "workspace-1"})

    assert FakeClient.session_calls == 1
    assert FakeClient.capture_calls == 1
    assert FakeClient.preflight_calls == 0


def test_regeneration_no_recall_reuses_input_without_session_capture_or_preflight(
    monkeypatch,
) -> None:
    monkeypatch.delenv("FAKE_MEMORIST_FAIL", raising=False)
    monkeypatch.setenv("FAKE_RUNTIME_PROFILE", "full")
    FakeClient.session_calls = FakeClient.capture_calls = FakeClient.preflight_calls = 0
    body = {
        "memorist": {"turn_policy": "no_recall", "attachment_review": False},
        "metadata": {
            "memorist_regeneration_uuid": "regen-1",
            "memorist_input_message_uuid": "original-message-1",
            "memorist_attachment_uuid": "stale-attachment",
            "memorist_workspace_uuid": "workspace-1",
        },
        "messages": [{"role": "user", "content": "original prompt"}],
    }

    result = _module().Filter().inlet(body, {"id": "user-1", "workspace_id": "workspace-1"})

    assert result["messages"][-1]["content"] == "original prompt"
    assert result["metadata"]["memorist_regeneration_uuid"] == "regen-1"
    assert result["metadata"]["memorist_input_message_uuid"] == "original-message-1"
    assert result["metadata"]["memorist_workspace_uuid"] == "workspace-1"
    assert "memorist_attachment_uuid" not in result["metadata"]
    assert FakeClient.session_calls == FakeClient.capture_calls == FakeClient.preflight_calls == 0


def test_assistant_response_captured_and_duplicate_deduped(monkeypatch) -> None:
    monkeypatch.delenv("FAKE_MEMORIST_FAIL", raising=False)
    FakeClient.assistant_calls = 0
    filter_instance = _module().Filter()
    body = {
        "id": "provider-1",
        "metadata": {
            "memorist_input_message_uuid": "message-1",
            "memorist_delivered_attachment_uuid": "a1",
            "memorist_user_uuid": "user-1",
            "memorist_workspace_uuid": "workspace-1",
        },
        "messages": [{"role": "assistant", "content": "assistant answer"}],
    }
    assert filter_instance.outlet(body, None) == body
    assert filter_instance.outlet(body, None) == body
    assert FakeClient.assistant_calls == 1


def test_valves_override_runtime_config(monkeypatch) -> None:
    monkeypatch.delenv("FAKE_MEMORIST_FAIL", raising=False)
    monkeypatch.delenv("MEMORIST_ENABLED", raising=False)
    filter_instance = _module().Filter()
    filter_instance.valves.memorist_core_url = "http://host.docker.internal:8777"
    filter_instance.valves.token_budget = 777
    filter_instance.valves.timeout_ms = 2500
    filter_instance.valves.retrieval_mode = "high_precision"
    body = {
        "conversation_id": "chat-1",
        "messages": [{"role": "user", "content": "hello"}],
    }

    filter_instance.inlet(body, {"id": "user-1", "workspace_id": "workspace-1"})

    assert FakeClient.last_config is not None
    assert FakeClient.last_config.core_url == "http://host.docker.internal:8777"
    assert FakeClient.last_config.attachment_token_budget == 777
    assert FakeClient.last_config.preflight_timeout_ms == 2500
    assert FakeClient.last_config.retrieval_mode == "high_precision"


def test_env_disable_takes_precedence_over_valve_default(monkeypatch) -> None:
    monkeypatch.setenv("MEMORIST_ENABLED", "false")
    FakeClient.last_config = None
    body = {"messages": [{"role": "user", "content": "hello"}]}

    result = _module().Filter().inlet(body, None)

    assert result == body
    assert FakeClient.last_config is None


def test_safe_payload_removes_secret_like_keys() -> None:
    safe = _module()._safe_payload(
        {
            "content": "hello",
            "api_key": "hidden",
            "auth_token": "hidden",
            "password": "hidden",
            "credential_id": "hidden",
        }
    )

    assert safe == {"content": "hello"}


def test_load_config_reads_current_environment(monkeypatch) -> None:
    config_module = importlib.import_module("shared.config")
    monkeypatch.setenv("MEMORIST_CORE_URL", "http://memorist-core:8777")
    monkeypatch.setenv("MEMORIST_PREFLIGHT_TIMEOUT_MS", "3456")

    loaded = config_module.load_config()

    assert loaded.core_url == "http://memorist-core:8777"
    assert loaded.preflight_timeout_ms == 3456


def test_parser_handles_temp_chat_and_model_metadata() -> None:
    parser = importlib.import_module("shared.payload_parser")
    body = {
        "temporary_chat_id": "tmp-1",
        "model": "small-model",
        "model_context_window": 4096,
        "messages": [{"role": "user", "id": "u1", "content": "hello"}],
        "metadata": {"client_session_nonce": "nonce-1"},
    }

    parsed = parser.parse_inlet_body(body, {"id": "user-1"})

    assert parsed.temporary_chat_id == "tmp-1"
    assert parsed.client_session_nonce == "nonce-1"
    assert parsed.target_model == "small-model"
    assert parsed.model_context_window == 4096
    assert parsed.first_message_hash is not None


def test_unsupported_body_shape_fail_opens_attachment_insert() -> None:
    parser = importlib.import_module("shared.payload_parser")
    body = {"messages": "unsupported"}

    result = parser.insert_memory_attachment(body, "memory", "attachment-1")

    assert result["messages"] == "unsupported"
    assert result["metadata"]["memorist_attachment_warning"] == (
        "unsupported_body_shape_messages_not_list"
    )


def test_contract_payload_fixtures_parse() -> None:
    parser = importlib.import_module("shared.payload_parser")
    fixture_root = ROOT.parent / "compatibility" / "payload_fixtures"
    inlet = json.loads((fixture_root / "inlet_new_chat_temp_id.ijson").read_text(encoding="utf-8"))
    outlet = json.loads(
        (fixture_root / "outlet_assistant_completed.ijson").read_text(encoding="utf-8")
    )

    parsed_inlet = parser.parse_inlet_body(inlet, {"id": "user-1"})
    parsed_outlet = parser.parse_outlet_body(outlet, {"id": "user-1"})

    assert parsed_inlet.temporary_chat_id == "tmp-chat-1"
    assert parsed_inlet.first_message_hash is not None
    assert parsed_outlet.assistant_text == "assistant answer"


def test_private_workflow_clears_stale_context_and_skips_all_memory_calls(
    monkeypatch,
) -> None:
    monkeypatch.delenv("FAKE_MEMORIST_FAIL", raising=False)
    FakeClient.session_calls = FakeClient.capture_calls = FakeClient.preflight_calls = 0
    body = {
        "conversation_id": "chat-off",
        "memorist": {"turn_policy": "private"},
        "metadata": {
            "memorist_delivered_attachment_uuid": "stale-attachment",
            "memorist_approved_attachment_uuid": "stale-approved",
            "memorist_input_message_uuid": "stale-input",
            "memorist_session_uuid": "stale-session",
        },
        "messages": [
            {
                "role": "system",
                "name": "memorist_context",
                "content": "stale private memory",
            },
            {"role": "user", "id": "user-off", "content": "do not remember this"},
        ],
    }

    result = (
        _module()
        .Filter()
        .inlet(
            body,
            {"id": "user-1", "workspace_id": "workspace-1"},
        )
    )

    assert result["messages"] == [
        {"role": "user", "id": "user-off", "content": "do not remember this"}
    ]
    assert FakeClient.session_calls == 0
    assert FakeClient.capture_calls == 0
    assert FakeClient.preflight_calls == 0
    assert result["metadata"]["memorist_memory_workflow"] == "off"
    assert result["metadata"]["memorist_capture_enabled"] is False
    assert result["metadata"]["memorist_recall_enabled"] is False
    assert result["metadata"]["memorist_attachment_enabled"] is False
    assert result["metadata"]["memorist_private"] is True
    for key in (
        "memorist_delivered_attachment_uuid",
        "memorist_approved_attachment_uuid",
        "memorist_input_message_uuid",
        "memorist_session_uuid",
    ):
        assert key not in result["metadata"]


def test_trusted_host_chat_id_overrides_browser_conversation_id(monkeypatch) -> None:
    monkeypatch.delenv("FAKE_MEMORIST_FAIL", raising=False)
    FakeClient.last_policy_chat_id = None
    FakeClient.last_session_conversation_id = None
    body = {
        "conversation_id": "browser-spoofed-chat",
        "messages": [{"role": "user", "id": "user-1", "content": "hello"}],
    }

    _module().Filter().inlet(
        body,
        {"id": "user-1", "workspace_id": "workspace-1"},
        {"chat_id": "host-canonical-chat"},
    )

    assert FakeClient.last_policy_chat_id == "host-canonical-chat"
    assert FakeClient.last_session_conversation_id == "host-canonical-chat"
    assert body["metadata"]["memorist_trusted_conversation_id"] == "host-canonical-chat"


def test_stale_context_is_replaced_once_when_memory_is_on(monkeypatch) -> None:
    monkeypatch.delenv("FAKE_MEMORIST_FAIL", raising=False)
    monkeypatch.setenv("FAKE_PREFLIGHT_STATUS", "attached")
    body = {
        "conversation_id": "chat-on",
        "metadata": {"memorist_delivered_attachment_uuid": "stale"},
        "messages": [
            {"role": "system", "name": "memorist_context", "content": "stale"},
            {"role": "user", "id": "fresh-user", "content": "fresh turn"},
        ],
    }

    result = (
        _module()
        .Filter()
        .inlet(
            body,
            {"id": "user-1", "workspace_id": "workspace-1"},
        )
    )

    contexts = [
        message
        for message in result["messages"]
        if isinstance(message, dict) and message.get("name") == "memorist_context"
    ]
    assert len(contexts) == 1
    assert "safe memory" in contexts[0]["content"]
    assert result["metadata"]["memorist_memory_workflow"] == "on"
    assert result["metadata"]["memorist_delivered_attachment_uuid"] == "attachment-1"
