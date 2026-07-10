from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import pytest

from memcore.model_control.providers.openai_compatible import OpenAICompatibleLLMProvider


def test_openai_compatible_health_check_validates_json_mode(
    openai_json_mode_server: tuple[str, type[BaseHTTPRequestHandler]],
) -> None:
    endpoint, handler = openai_json_mode_server
    provider = OpenAICompatibleLLMProvider(
        endpoint,
        "mock-chat",
        supports_json_mode=True,
        requires_structured_extraction=True,
    )

    health = provider.health_check()

    assert health.status == "ok"
    assert health.detail == "HTTP 200; chat completions validated"
    assert handler.last_payload["response_format"] == {"type": "json_object"}


def test_openai_compatible_health_check_warns_without_structured_flags(
    openai_json_mode_server: tuple[str, type[BaseHTTPRequestHandler]],
) -> None:
    endpoint, handler = openai_json_mode_server
    provider = OpenAICompatibleLLMProvider(
        endpoint,
        "mock-chat",
        requires_structured_extraction=True,
    )

    health = provider.health_check()

    assert health.status == "ok"
    assert "response_format" not in handler.last_payload
    assert health.detail is not None
    assert "chat completions validated" in health.detail
    assert "may be unsuitable for structured memory tasks" in health.detail


def test_openai_compatible_health_check_reports_json_mode_unsupported(
    openai_json_mode_server: tuple[str, type[BaseHTTPRequestHandler]],
) -> None:
    endpoint, handler = openai_json_mode_server
    handler.reject_response_format = True
    provider = OpenAICompatibleLLMProvider(
        endpoint,
        "mock-chat",
        supports_structured_output=True,
        requires_structured_extraction=True,
    )

    health = provider.health_check()

    assert health.status == "error"
    assert health.detail == (
        "Provider rejected JSON response_format; disable Supports JSON mode or "
        "choose a compatible model."
    )


def test_openai_compatible_health_check_redacts_auth_failure(
    openai_json_mode_server: tuple[str, type[BaseHTTPRequestHandler]],
) -> None:
    endpoint, handler = openai_json_mode_server
    handler.auth_failure_body = (
        "Authorization failed for Bearer abc.def.ghi; "
        "api_key=sk-test-token token=plain-token secret: super-secret "
        "url=https://user:pass@example.test/v1?token=query-token"
    )
    provider = OpenAICompatibleLLMProvider(endpoint, "mock-chat")

    health = provider.health_check()

    assert health.status == "error"
    assert health.detail is not None
    assert "HTTP 401" in health.detail
    _assert_no_fake_secret_material(health.detail)
    assert "Bearer [redacted]" in health.detail
    assert "api_key=[redacted]" in health.detail
    assert "token=[redacted]" in health.detail
    assert "secret: [redacted]" in health.detail
    assert "https://example.test/v1?token=%5Bredacted%5D" in health.detail


def test_openai_compatible_health_check_reports_missing_secret_env_var(
    monkeypatch: pytest.MonkeyPatch,
    openai_json_mode_server: tuple[str, type[BaseHTTPRequestHandler]],
) -> None:
    endpoint, handler = openai_json_mode_server
    secret_env_var_name = "MEMORIST_TEST_OPENAI_API_KEY"
    monkeypatch.delenv(secret_env_var_name, raising=False)
    provider = OpenAICompatibleLLMProvider(
        endpoint,
        "mock-chat",
        secret_env_var_name=secret_env_var_name,
    )

    health = provider.health_check()

    assert health.status == "error"
    assert health.detail == (f"Secret environment variable {secret_env_var_name} is not set")
    assert handler.last_payload == {}


@pytest.fixture
def openai_json_mode_server() -> Iterator[tuple[str, type[BaseHTTPRequestHandler]]]:
    class JsonModeHandler(BaseHTTPRequestHandler):
        reject_response_format = False
        auth_failure_body: str | None = None
        last_payload: dict[str, Any] = {}

        def do_POST(self) -> None:  # noqa: N802
            if self.path != "/v1/chat/completions":
                self.send_response(404)
                self.end_headers()
                return
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            type(self).last_payload = payload
            if type(self).auth_failure_body is not None:
                body = type(self).auth_failure_body.encode("utf-8")
                self.send_response(401)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if type(self).reject_response_format and "response_format" in payload:
                body = json.dumps({"error": "response_format is unsupported"}).encode("utf-8")
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            content = json.dumps({"memorist_provider_test": "ok"})
            body = json.dumps({"choices": [{"message": {"content": content}}]}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:
            return

    server = HTTPServer(("127.0.0.1", 0), JsonModeHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", JsonModeHandler
    finally:
        server.shutdown()
        thread.join(timeout=2)


def _assert_no_fake_secret_material(text: str) -> None:
    for secret in (
        "abc.def.ghi",
        "sk-test-token",
        "plain-token",
        "super-secret",
        "user:pass",
        "query-token",
    ):
        assert secret not in text
