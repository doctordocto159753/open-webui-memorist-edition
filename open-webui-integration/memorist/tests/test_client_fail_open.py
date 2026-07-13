from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.client import MemoristClient
from shared.config import MemoristIntegrationConfig
from shared.errors import UnsafeMemoristUrl, sanitize_error


def test_fail_open_defaults_true() -> None:
    config = MemoristIntegrationConfig()
    assert config.fail_open is True
    assert config.preflight_enabled is True


def test_client_rejects_remote_url() -> None:
    try:
        MemoristClient(MemoristIntegrationConfig(core_url="https://example.com"))
    except UnsafeMemoristUrl:
        return
    raise AssertionError("remote URL accepted")


def test_error_sanitizer_redacts_secret_like_text() -> None:
    assert sanitize_error("token=" + "REDACTED_TEST_VALUE") == "[redacted]"


def test_real_client_signs_policy_and_session_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[urllib.request.Request] = []

    class Response:
        def __init__(self, payload: dict[str, Any]) -> None:
            self.payload = payload

        def __enter__(self) -> Response:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self) -> bytes:
            return json.dumps(self.payload).encode()

    def urlopen(request: urllib.request.Request, **_kwargs: Any) -> Response:
        requests.append(request)
        if request.full_url.endswith("/policy/resolve"):
            return Response(
                {
                    "policy": {
                        "mode": "no_recall",
                        "capture_enabled": True,
                        "recall_enabled": False,
                        "attachment_enabled": False,
                        "private": False,
                    },
                    "source": "turn",
                    "runtime_profile": "lite",
                }
            )
        return Response(
            {
                "session_uuid": "session-1",
                "workspace_uuid": "workspace-1",
                "project_uuid": "project-1",
            }
        )

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    client = MemoristClient(
        MemoristIntegrationConfig(
            actor_assertion_secret="assertion-secret",
            actor_service_token="service-secret",
        )
    )
    client.resolve_turn_policy(
        user_id="user-1",
        chat_id="chat-1",
        workspace_id="workspace-1",
        request_control={"turn_policy": "no_recall"},
    )
    client.resolve_session(
        "chat-1",
        "Chat",
        "user-1",
        "workspace-1",
        turn_policy="no_recall",
    )

    assert len(requests) == 2
    for request in requests:
        assert request.get_header("X-memorist-service-token") == "service-secret"
        assert request.get_header("X-memorist-actor-assertion")
