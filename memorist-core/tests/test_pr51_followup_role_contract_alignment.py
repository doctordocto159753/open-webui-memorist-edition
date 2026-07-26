from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, ClassVar

import pytest

from memcore.memory_worker.attempt_audit import (
    FrozenProviderExecution,
    ProviderAttemptAuditRepository,
)
from memcore.memory_worker.execution import (
    DeterministicContractError,
    NoDeterministicFallbackError,
)
from memcore.memory_worker.providers.openai_compatible import ProviderAttempt
from memcore.memory_worker.service import _is_terminal_memory_job_error
from memcore.model_control.registry import test_profile_health
from memcore.model_control.role_contracts import role_contract_manifest
from memcore.model_control.runtime_contracts import runtime_contract_for_role
from memcore.model_control.providers.runtime_openai import (
    RuntimeContractOpenAICompatibleLLMProvider,
)
from memcore.models import ModelRole


class _RoleContractHandler(BaseHTTPRequestHandler):
    requests: ClassVar[list[dict[str, Any]]] = []
    role_output: ClassVar[dict[str, Any]] = {}

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        type(self).requests.append(payload)
        messages = payload.get("messages") or []
        prompt = "\n".join(str(item.get("content") or "") for item in messages)
        output = (
            {"memorist_provider_test": "ok"}
            if "memorist_provider_test" in prompt
            else type(self).role_output
        )
        body = json.dumps(
            {
                "id": f"role-contract-{len(type(self).requests)}",
                "choices": [{"message": {"content": json.dumps(output)}}],
                "usage": {"prompt_tokens": 13, "completion_tokens": 7},
            }
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        return


@pytest.fixture
def role_contract_server() -> Iterator[type[_RoleContractHandler]]:
    _RoleContractHandler.requests = []
    _RoleContractHandler.role_output = {}
    server = ThreadingHTTPServer(("127.0.0.1", 0), _RoleContractHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    _RoleContractHandler.port = server.server_address[1]  # type: ignore[attr-defined]
    try:
        yield _RoleContractHandler
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _high_confidence_output() -> dict[str, Any]:
    return {
        "decision": "approved",
        "confidence": 0.93,
        "reason_codes": ["evidence_alignment"],
        "evidence_spans": [{"start": 0, "end": 21, "text": "Backups stay enabled."}],
    }


def _profile(handler: type[_RoleContractHandler], *, structured: bool) -> dict[str, Any]:
    return {
        "provider_type": "openai_compatible_llm",
        "provider_name": "controlled",
        "model_name": "controlled-model",
        "role": ModelRole.HIGH_CONFIDENCE_EXTRACTION.value,
        "endpoint_url": f"http://127.0.0.1:{handler.port}/v1",  # type: ignore[attr-defined]
        "endpoint_is_local": True,
        "secret_strategy": "none",
        "supports_json_mode": not structured,
        "supports_structured_output": structured,
        "is_enabled": True,
    }


def test_role_manifest_uses_actual_stage_runtime_contract() -> None:
    manifest = role_contract_manifest(ModelRole.HIGH_CONFIDENCE_EXTRACTION)
    contract = runtime_contract_for_role(ModelRole.HIGH_CONFIDENCE_EXTRACTION)
    assert contract is not None
    assert manifest["execution"] == "stage_invoker"
    assert manifest["contract_hash"] == contract.contract_hash
    assert manifest["runtime_contract"]["contract_id"] == (
        "memorist.runtime.high_confidence_extraction"
    )
    assert "memorist.unit_analysis" not in json.dumps(manifest)


@pytest.mark.parametrize(
    ("role", "prompt_version"),
    [
        (ModelRole.HIGH_CONFIDENCE_EXTRACTION, "1.0"),
        (ModelRole.PRIVACY_SENSITIVITY, "2.0"),
        (ModelRole.BLOCK_COMPACTION, "2.0"),
    ],
)
def test_certification_prompt_matches_runtime_stage_identity(
    role: ModelRole,
    prompt_version: str,
) -> None:
    contract = runtime_contract_for_role(role)
    assert contract is not None
    prompt = contract.render_probe_prompt()
    assert f"ROLE={role.value}" in prompt
    assert f"STAGE={contract.stage}" in prompt
    assert f"PROMPT_VERSION={prompt_version}" in prompt


def test_runtime_role_sends_strict_json_schema(
    role_contract_server: type[_RoleContractHandler],
) -> None:
    contract = runtime_contract_for_role(ModelRole.HIGH_CONFIDENCE_EXTRACTION)
    assert contract is not None
    role_contract_server.role_output = _high_confidence_output()
    provider = RuntimeContractOpenAICompatibleLLMProvider(
        f"http://127.0.0.1:{role_contract_server.port}/v1",  # type: ignore[attr-defined]
        "controlled-model",
        supports_structured_output=True,
        requires_structured_extraction=True,
    )
    response = provider.complete_structured(contract.render_probe_prompt(), timeout_seconds=5)
    assert response.output_ijson == _high_confidence_output()
    request = role_contract_server.requests[0]
    response_format = request["response_format"]
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["strict"] is True
    assert response_format["json_schema"]["name"] == contract.schema_name
    assert response_format["json_schema"]["schema"] == contract.json_schema


def test_runtime_role_json_mode_carries_contract_schema(
    role_contract_server: type[_RoleContractHandler],
) -> None:
    contract = runtime_contract_for_role(ModelRole.HIGH_CONFIDENCE_EXTRACTION)
    assert contract is not None
    role_contract_server.role_output = _high_confidence_output()
    provider = RuntimeContractOpenAICompatibleLLMProvider(
        f"http://127.0.0.1:{role_contract_server.port}/v1",  # type: ignore[attr-defined]
        "controlled-model",
        supports_json_mode=True,
        requires_structured_extraction=True,
    )
    provider.complete_structured(contract.render_probe_prompt(), timeout_seconds=5)
    request = role_contract_server.requests[0]
    assert request["response_format"] == {"type": "json_object"}
    prompt = request["messages"][0]["content"]
    assert f"RUNTIME_CONTRACT_ID={contract.contract_id}" in prompt
    assert "JSON_SCHEMA=" in prompt


def test_certification_executes_the_same_runtime_contract(
    role_contract_server: type[_RoleContractHandler],
) -> None:
    contract = runtime_contract_for_role(ModelRole.HIGH_CONFIDENCE_EXTRACTION)
    assert contract is not None
    role_contract_server.role_output = _high_confidence_output()
    health = test_profile_health(_profile(role_contract_server, structured=True), timeout_seconds=5)
    assert len(role_contract_server.requests) == 2
    assert health.overall_status == "ok"
    assert health.role_probe_status == "compatible"
    assert health.role_probe_contract_hash == contract.contract_hash
    role_request = role_contract_server.requests[1]
    assert role_request["response_format"]["type"] == "json_schema"
    assert role_request["response_format"]["json_schema"]["schema"] == contract.json_schema


def test_certification_rejects_connectivity_only_success(
    role_contract_server: type[_RoleContractHandler],
) -> None:
    role_contract_server.role_output = {"status": "ok"}
    health = test_profile_health(_profile(role_contract_server, structured=True), timeout_seconds=5)
    assert len(role_contract_server.requests) == 2
    assert health.overall_status == "incompatible"
    assert health.role_probe_status == "incompatible"
    assert health.failure_stage == "role_contract_probe"


def test_attempt_reservation_distinguishes_completed_from_unknown() -> None:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute(
        """
        CREATE TABLE processing_provider_attempts (
          provider_attempt_uuid TEXT PRIMARY KEY,
          stage_execution_uuid TEXT NOT NULL,
          processing_run_uuid TEXT,
          job_uuid TEXT,
          source_type TEXT NOT NULL,
          source_uuid TEXT NOT NULL,
          attempt_index INTEGER NOT NULL,
          attempt_kind TEXT NOT NULL,
          requested_role TEXT NOT NULL,
          effective_role TEXT NOT NULL,
          model_profile_uuid TEXT,
          profile_fingerprint TEXT NOT NULL,
          scope_source TEXT NOT NULL,
          inheritance_source TEXT,
          provider_type TEXT NOT NULL,
          model_name TEXT NOT NULL,
          capability_mode TEXT NOT NULL,
          prompt_id TEXT NOT NULL,
          prompt_version TEXT NOT NULL,
          contract_hash TEXT NOT NULL,
          input_hash TEXT NOT NULL,
          response_output_hash TEXT,
          provider_response_id TEXT,
          http_status INTEGER,
          transport_status TEXT NOT NULL,
          parse_status TEXT NOT NULL,
          schema_valid INTEGER,
          canonicalized INTEGER NOT NULL,
          validation_errors_ijson TEXT NOT NULL,
          input_tokens INTEGER NOT NULL DEFAULT 0,
          output_tokens INTEGER NOT NULL DEFAULT 0,
          latency_ms INTEGER NOT NULL DEFAULT 0,
          started_at TEXT NOT NULL,
          completed_at TEXT,
          idempotency_identity TEXT NOT NULL UNIQUE,
          schema_version INTEGER NOT NULL,
          UNIQUE(stage_execution_uuid, attempt_index)
        )
        """
    )
    frozen = FrozenProviderExecution(
        stage_execution_uuid="stage-1",
        processing_run_uuid="run-1",
        job_uuid="job-1",
        source_type="message",
        source_uuid="message-1",
        requested_role="memory_extraction",
        effective_role="memory_extraction",
        model_profile_uuid="profile-1",
        profile_fingerprint="profile-fingerprint",
        scope_source="global_default",
        inheritance_source=None,
        provider_type="openai_compatible_llm",
        model_name="controlled-model",
        capability_mode="json_schema",
        prompt_id="memorist.jakobson_sentence_analysis",
        prompt_version="3.0",
        contract_hash="contract-hash",
        input_hash="input-hash",
        idempotency_identity="stage-authority",
        deterministic_fallback_version="jakobson-deterministic-v1",
    )
    audit = ProviderAttemptAuditRepository(connection, frozen, postgres=False)
    assert audit.reserve(1, "initial") == "reserved"
    assert audit.reserve(1, "initial") == "unknown_completion"
    audit.finalize(
        1,
        ProviderAttempt(
            parsed={"status": "ok"},
            parse_error=None,
            input_tokens=3,
            output_tokens=2,
            latency_ms=5,
            provider_response_id="response-1",
            http_status=200,
        ),
        schema_valid=True,
        canonicalized=False,
        validation_issues=[],
    )
    assert audit.reserve(1, "initial") == "completed"
    connection.close()


@pytest.mark.parametrize(
    "error",
    [
        DeterministicContractError([{"path": "items", "code": "invalid"}]),
        NoDeterministicFallbackError("no_safe_fallback"),
    ],
)
def test_internal_contract_failures_are_terminal(error: Exception) -> None:
    assert _is_terminal_memory_job_error(error) is True
    assert _is_terminal_memory_job_error(RuntimeError("transient")) is False
