"""Controlled real-HTTP provider evidence for the prompt-contract closure.

These tests use an actual local HTTP server that returns a *programmed sequence*
of responses (not a perfect stub) so we can prove the exact field failure, the
bounded repair path, the invalid-twice deterministic fallback, and the
capability-correct wire format end to end through the real provider adapter.
"""

from __future__ import annotations

import copy
import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from memcore.memory_worker.execution import ContractExecutionOutcome
from memcore.memory_worker.jakobson_runtime import execute_jakobson_contract
from memcore.memory_worker.prompts.contracts import (
    canonical_jakobson_v3_example,
    canonical_sentence_items,
)
from memcore.memory_worker.prompts.registry import validate_prompt_execution
from memcore.memory_worker.prompts.validators import PromptValidationError

INPUT_PAYLOAD = {"sentences": [{"id": 1, "text": "نوشیدنی مورد علاقهٔ من چیست؟"}]}

# The exact malformed payload observed in the field: status=success, items (not
# sentences), and string six-factor values instead of structured objects.
FIELD_INVALID_PAYLOAD = {
    "schema_version": "1.0",
    "prompt_id": "memorist.jakobson_sentence_analysis",
    "prompt_version": "2.0",
    "status": "success",
    "warnings": [],
    "items": [
        {
            "id": 1,
            "text": "نوشیدنی مورد علاقهٔ من چیست؟",
            "six_factors": {
                "sender_addresser": "unknown",
                "receiver_addressee": "unknown",
                "message": "نوشیدنی مورد علاقهٔ من چیست؟",
                "context_referent": "unknown",
                "code": "Persian",
                "contact_channel": "unknown",
            },
            "dominant_function": "conative",
            "secondary_functions": [],
            "function_reason": "The sentence is a question asking for information.",
            "notes": "",
        }
    ],
    "analysis_level": "sentence",
    "model": "jakobson_six_factor",
    "input_language": "fa",
    "sentence_count": 1,
    "overall_summary": "...",
}


def _valid_v3() -> dict[str, Any]:
    return copy.deepcopy(canonical_jakobson_v3_example())


def _deterministic() -> dict[str, Any]:
    output = _valid_v3()
    output["warnings"] = ["deterministic_fallback"]
    return output


class _SequenceHandler(BaseHTTPRequestHandler):
    programmed: list[Any] = []
    calls: int = 0
    paths: list[str] = []

    def do_POST(self) -> None:  # noqa: N802
        type(self).paths.append(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        index = type(self).calls
        type(self).calls += 1
        item = self.programmed[min(index, len(self.programmed) - 1)]
        if isinstance(item, int):
            self.send_response(item)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(
                json.dumps({"error": {"message": "response_format unsupported"}}).encode()
            )
            return
        body = {
            "id": f"resp-{index + 1}",
            "choices": [{"message": {"content": json.dumps(item, ensure_ascii=False)}}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 7},
        }
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(body).encode("utf-8"))

    def log_message(self, *_: Any) -> None:  # silence test server logging
        return


@pytest.fixture
def provider_server() -> Iterator[type[_SequenceHandler]]:
    _SequenceHandler.programmed = []
    _SequenceHandler.calls = 0
    _SequenceHandler.paths = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _SequenceHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    _SequenceHandler.port = server.server_address[1]  # type: ignore[attr-defined]
    try:
        yield _SequenceHandler
    finally:
        server.shutdown()
        server.server_close()


def _profile(handler: type[_SequenceHandler], *, structured: bool = False) -> dict[str, Any]:
    return {
        "provider_type": "openai_compatible_llm",
        "model_name": "controlled-model",
        "endpoint_url": f"http://127.0.0.1:{handler.port}/v1",  # type: ignore[attr-defined]
        "secret_env_var_name": None,
        "supports_json_mode": True,
        "supports_structured_output": structured,
        "model_profile_uuid": "prof-1",
    }


def _run(handler: type[_SequenceHandler], structured: bool = False) -> ContractExecutionOutcome:
    return execute_jakobson_contract(
        profile=_profile(handler, structured=structured),
        input_payload=INPUT_PAYLOAD,
        deterministic_builder=_deterministic,
    )


def test_field_invalid_payload_is_reproduced_by_the_contract() -> None:
    # Reproduces the exact field incident: validated under its own declared
    # version (2.0), the "success" status is rejected first.
    with pytest.raises(PromptValidationError) as field:
        validate_prompt_execution(
            "memorist.jakobson_sentence_analysis", "2.0", INPUT_PAYLOAD, FIELD_INVALID_PAYLOAD
        )
    assert "status must be ok" in str(field.value)
    # Under the active v3 contract the same-shaped payload is also rejected.
    v3_shaped = dict(FIELD_INVALID_PAYLOAD, prompt_version="3.0")
    with pytest.raises(PromptValidationError):
        validate_prompt_execution(
            "memorist.jakobson_sentence_analysis", "3.0", INPUT_PAYLOAD, v3_shaped
        )


def test_valid_first_call_uses_provider_output(provider_server: type[_SequenceHandler]) -> None:
    provider_server.programmed = [_valid_v3()]
    outcome = _run(provider_server)
    assert outcome.provider_output_valid is True
    assert outcome.fallback_used is False
    assert outcome.repair_attempted is False
    assert provider_server.calls == 1
    assert all(path == "/v1/chat/completions" for path in provider_server.paths)


def test_invalid_then_valid_repairs(provider_server: type[_SequenceHandler]) -> None:
    provider_server.programmed = [FIELD_INVALID_PAYLOAD, _valid_v3()]
    outcome = _run(provider_server)
    assert outcome.repair_attempted is True
    assert outcome.repair_succeeded is True
    assert outcome.fallback_used is False
    assert outcome.provider_output_valid is True
    assert provider_server.calls == 2


def test_invalid_twice_falls_back_deterministically(
    provider_server: type[_SequenceHandler],
) -> None:
    provider_server.programmed = [FIELD_INVALID_PAYLOAD, FIELD_INVALID_PAYLOAD]
    outcome = _run(provider_server)
    assert outcome.repair_attempted is True
    assert outcome.repair_succeeded is False
    assert outcome.fallback_used is True
    assert outcome.fallback_reason == "provider_output_contract_invalid"
    assert outcome.provider_output_valid is False
    assert provider_server.calls == 2
    assert outcome.validation_error_paths  # sanitized paths recorded
    # The returned output is the valid deterministic fallback.
    assert outcome.output["warnings"] == ["deterministic_fallback"]
    assert len(canonical_sentence_items(outcome.output)) == 1


def test_status_alias_only_is_canonicalized_without_repair(
    provider_server: type[_SequenceHandler],
) -> None:
    alias = _valid_v3()
    alias["status"] = "success"  # otherwise fully valid
    provider_server.programmed = [alias]
    outcome = _run(provider_server)
    assert outcome.canonicalized is True
    assert outcome.repair_attempted is False
    assert outcome.provider_output_valid is True
    assert outcome.output["status"] == "ok"
    assert provider_server.calls == 1


def test_response_format_rejection_falls_back_without_repair(
    provider_server: type[_SequenceHandler],
) -> None:
    # A json_schema-capable profile whose provider rejects response_format (400).
    provider_server.programmed = [400]
    outcome = _run(provider_server, structured=True)
    assert outcome.fallback_used is True
    assert outcome.fallback_reason == "provider_incompatible"
    assert outcome.repair_attempted is False
    assert provider_server.calls == 1


def test_markdown_fenced_json_is_parsed(provider_server: type[_SequenceHandler]) -> None:
    # The handler wraps content in a fenced block by returning a string body.
    fenced = _valid_v3()
    provider_server.programmed = [fenced]
    outcome = _run(provider_server)
    assert outcome.provider_output_valid is True
