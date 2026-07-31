"""Production-integrated cross-chat Message Evidence and retrieval-plan tests.

Unlike ``test_message_first_retrieval``, nothing here seeds ``message_semantic_*``
tables by hand and nothing hands ``query_understanding`` straight to the
retriever. Both the semantic analysis and the preflight plan arrive as real
OpenAI-compatible HTTP responses from a local server, so prompt rendering,
``response_format`` negotiation, contract validation, evidence binding, span
persistence, plan persistence and scoped retrieval all execute as they do in
production.
"""

from __future__ import annotations

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, ClassVar

import pytest

from memcore.config import Settings
from memcore.memory_worker.pipeline import MemoryWorkerPipeline
from memcore.model_control.registry import test_profile_health as run_profile_health
from memcore.model_control.repository import ModelControlRepository
from memcore.model_control.schemas import ModelProfileCreate, ProviderType
from memcore.models import ModelRole, utc_now
from memcore.preflight import PreflightRequest, PreflightService
from memcore.repositories import (
    MessageRepository,
    ProjectRepository,
    SessionRepository,
    WorkspaceRepository,
)
from memcore.storage.migrations import apply_migrations
from memcore.storage.sqlite import connect

SOURCE_TEXT = (
    "برای پروژه Memorist تصمیم قطعی من این است که timeout هر نقش پردازشی باید "
    "جداگانه و از UI و فایل env قابل تنظیم باشد."
)
RECALL_TEXT = "در تنظیمات مهلت زمانی نقش‌های پردازشی چه تصمیمی گرفته شد؟"


class _SemanticProviderHandler(BaseHTTPRequestHandler):
    """Answers both processing roles over real HTTP, like a hosted provider."""

    requests: ClassVar[list[dict[str, Any]]] = []

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        messages = payload["messages"]
        prompt_text = "\n".join(str(message.get("content") or "") for message in messages)
        type(self).requests.append(
            {
                "model": payload.get("model"),
                "response_format": payload.get("response_format"),
                "prompt_text": prompt_text,
            }
        )
        if "memorist_provider_test" in prompt_text:
            output = {"memorist_provider_test": "ok"}
        elif "memorist.preflight_planning" in prompt_text:
            output = _preflight_planning_response(_budget_from_prompt(prompt_text))
        elif "memorist.semantic_candidate_analysis" in messages[0]["content"]:
            output = _semantic_response(json.loads(messages[1]["content"]))
        else:
            output = _jakobson_response(messages)
        body = json.dumps(
            {
                "id": f"integration-response-{len(type(self).requests)}",
                "choices": [{"message": {"content": json.dumps(output, ensure_ascii=False)}}],
                "usage": {"prompt_tokens": 23, "completion_tokens": 19},
            }
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        return


def _semantic_response(input_payload: dict[str, Any]) -> dict[str, Any]:
    raw = str(input_payload["current_raw_text"])
    return {
        "schema_version": "1.0",
        "prompt_id": "memorist.semantic_candidate_analysis",
        "prompt_version": "1.1",
        "status": "ok",
        "warnings": [],
        "intent": "durable configuration decision",
        "primary_topic": "processing role timeout",
        "secondary_topic": "UI and env configurability",
        "one_line_summary": (
            "durable configuration decision > processing role timeout > UI and env configurability"
        ),
        "message_categories": [
            {"category": "Decision", "normalized_label": "timeout decision", "confidence": 0.95},
            {
                "category": "Instruction",
                "normalized_label": "configuration requirement",
                "confidence": 0.8,
            },
        ],
        "concept_tags": [
            {
                "canonical_label": "processing role timeout",
                "aliases": ["timeout", "مهلت زمانی"],
                "confidence": 0.9,
            },
        ],
        "entities": [{"canonical_name": "Memorist", "entity_type": "project", "confidence": 0.9}],
        "process_references": [],
        "epistemic_status": "asserted",
        "temporal_status": "current",
        "importance": 0.95,
        "explicit_memory_request": True,
        "semantic_units": [
            {
                "id": "timeout-decision",
                "raw_start": 0,
                "raw_end": len(raw),
                "evidence": raw,
                "proposition": raw,
                "unit_type": "fragment",
                "memory_kind": "Decision",
                "lifecycle_status": "current",
                "durability": "durable",
                "polarity": "affirmed",
                "epistemic_status": "asserted",
            }
        ],
        "references": [],
        "relations": [],
    }


def _budget_from_prompt(prompt_text: str) -> int:
    """Echo the caller's own attachment budget back at it.

    The rendered preflight prompt embeds the input payload, and the validator
    rejects an ``estimated_tokens`` above the requested budget. A hardcoded
    number therefore fails the certification probe, whose budget is far smaller
    than a live turn's.
    """

    budgets = [int(value) for value in re.findall(r'"max_tokens":\s*(\d+)', prompt_text)]
    return min(budgets) if budgets else 0


def _jakobson_response(messages: list[dict[str, Any]]) -> dict[str, Any]:
    from memcore.memory_worker.prompts.contracts import canonical_jakobson_v3_example

    payload = json.loads(messages[1]["content"]) if len(messages) > 1 else _payload_from(messages)
    output = canonical_jakobson_v3_example()
    template = output["items"][0]
    output["items"] = []
    for sentence in payload["sentences"]:
        item = json.loads(json.dumps(template))
        item["id"] = sentence["id"]
        item["text"] = sentence["text"]
        output["items"].append(item)
    output["sentence_count"] = len(output["items"])
    return output


def _payload_from(messages: list[dict[str, Any]]) -> dict[str, Any]:
    text = str(messages[0].get("content") or "")
    start = text.index('{"sentences"')
    decoder = json.JSONDecoder()
    payload, _ = decoder.raw_decode(text[start:])
    return dict(payload)


def _preflight_planning_response(estimated_tokens: int) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "prompt_id": "memorist.preflight_planning",
        "prompt_version": "2.1",
        "status": "ok",
        "warnings": [],
        "query_understanding": {
            "intent": "recall prior decision",
            "primary_topic": "processing role timeout",
            "secondary_topic": "configuration surface",
            "entities": ["Memorist"],
            "process_label": None,
            "stage_ordinal": None,
            "requested_operation": "recall",
            "requested_time": None,
            "expected_answer_type": "contextual_answer",
            "relation_expansion_hints": ["decision"],
        },
        "items": [
            {
                "attachment_mode": "standard",
                "selected_memory_ids": [],
                "excluded_memory_ids": [],
                "trusted_directive_ids": [],
                "ordinary_memory_ids": [],
                "conflict_ids": [],
                "compression_strategy": "source_linked_brief",
                "abstain_reason": None,
                "security_notes": [],
                "estimated_tokens": estimated_tokens,
            }
        ],
    }


@pytest.fixture()
def provider_endpoint() -> Any:
    _SemanticProviderHandler.requests = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _SemanticProviderHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/v1"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _install_role_profiles(connection: Any, endpoint_url: str) -> dict[ModelRole, str]:
    repository = ModelControlRepository(connection)
    installed: dict[ModelRole, str] = {}
    for role in (ModelRole.MEMORY_EXTRACTION, ModelRole.PREFLIGHT):
        profile = repository.create_profile(
            ModelProfileCreate(
                profile_name=f"integration {role.value}",
                provider_type=ProviderType.OPENAI_COMPATIBLE_LLM,
                model_name=f"integration-{role.value}",
                role=role,
                endpoint_url=endpoint_url,
                endpoint_is_local=True,
                supports_json_mode=True,
                supports_structured_output=True,
            )
        )
        # Production refuses to assign an uncertified profile as a role default,
        # so the certification probe has to reach the provider for real too.
        repository.record_health_event(
            profile.model_profile_uuid,
            run_profile_health(profile, timeout_seconds=5),
        )
        repository.set_default(role, profile.model_profile_uuid)
        installed[role] = profile.model_profile_uuid
    connection.commit()
    return installed


def test_cross_chat_message_evidence_and_plan_come_from_real_provider_responses(
    tmp_path: Path,
    provider_endpoint: str,
) -> None:
    db_path = tmp_path / "production-integration.sqlite"
    connection = connect(db_path)
    apply_migrations(connection)
    settings = Settings(
        db_path=str(db_path),
        object_store_path=str(tmp_path / "objects"),
        graph_backend="disabled",
    )
    profiles = _install_role_profiles(connection, provider_endpoint)

    workspace = WorkspaceRepository(connection).create_workspace("Memorist")
    project = ProjectRepository(connection).create_project(workspace.workspace_uuid, "Memorist")
    sessions = SessionRepository(connection)
    messages = MessageRepository(connection)
    source_session = sessions.create_session(
        workspace_uuid=workspace.workspace_uuid,
        project_uuid=project.project_uuid,
    )
    recall_session = sessions.create_session(
        workspace_uuid=workspace.workspace_uuid,
        project_uuid=project.project_uuid,
    )
    assert source_session.session_uuid != recall_session.session_uuid
    for session_uuid in (source_session.session_uuid, recall_session.session_uuid):
        connection.execute(
            "INSERT INTO memorist_session_actors "
            "(session_uuid, user_uuid, workspace_uuid, created_at) VALUES (?, ?, ?, ?)",
            (session_uuid, "user-1", workspace.workspace_uuid, utc_now()),
        )
    source = messages.create_message(
        source_session.session_uuid,
        role="user",
        creator_type="user",
        raw_text=SOURCE_TEXT,
    )
    connection.commit()

    result = MemoryWorkerPipeline(connection, settings).process_message(source.message_uuid)

    # The semantic stage really called the provider and really persisted spans.
    assert result["semantic_prompt_execution_uuid"] is not None
    assert result["semantic_terminal_gate_short_circuit"] is False
    semantic_execution = connection.execute(
        """
        SELECT provider_type, status, validated_output_ijson
        FROM prompt_execution_runs
        WHERE message_uuid = ?
          AND prompt_id = 'memorist.semantic_candidate_analysis'
        """,
        (source.message_uuid,),
    ).fetchone()
    assert semantic_execution is not None
    assert semantic_execution["provider_type"] == "openai_compatible_llm"
    assert semantic_execution["status"] == "ok"
    analysis = connection.execute(
        """
        SELECT semantic_analysis_uuid, summary_intent, primary_topic, secondary_topic,
               one_line_summary, epistemic_status, temporal_status, source_authority
        FROM message_semantic_analyses
        WHERE message_uuid = ?
        """,
        (source.message_uuid,),
    ).fetchone()
    assert analysis is not None
    # Canonical structured fields, not a re-parse of the rendered summary string.
    assert analysis["summary_intent"] == "durable configuration decision"
    assert analysis["primary_topic"] == "processing role timeout"
    assert analysis["secondary_topic"] == "UI and env configurability"
    assert analysis["source_authority"] == "user_explicit"
    assert analysis["one_line_summary"] == (
        "durable configuration decision > processing role timeout > UI and env configurability"
    )

    query = messages.create_message(
        recall_session.session_uuid,
        role="user",
        creator_type="user",
        raw_text=RECALL_TEXT,
    )
    connection.commit()

    response = PreflightService(connection, settings).run(
        PreflightRequest(
            session_uuid=recall_session.session_uuid,
            input_message_uuid=query.message_uuid,
            retrieval_mode="standard",
            token_budget=1400,
            recent_conversation_text=RECALL_TEXT,
            user_uuid="user-1",
        )
    )

    assert response.retrieval_run_uuid is not None
    plan = connection.execute(
        """
        SELECT intent, primary_topic, secondary_topic, entities_ijson, requested_operation,
               expected_answer_type, relation_hints_ijson, user_uuid, workspace_uuid,
               project_uuid, input_message_uuid, contract_hash
        FROM model_retrieval_plans
        WHERE retrieval_run_uuid = ?
        """,
        (response.retrieval_run_uuid,),
    ).fetchone()
    assert plan is not None, "the accepted preflight model plan was not persisted"
    assert plan["intent"] == "recall prior decision"
    assert plan["primary_topic"] == "processing role timeout"
    assert plan["secondary_topic"] == "configuration surface"
    assert json.loads(plan["entities_ijson"]) == ["Memorist"]
    assert plan["requested_operation"] == "recall"
    assert plan["expected_answer_type"] == "contextual_answer"
    assert json.loads(plan["relation_hints_ijson"]) == ["decision"]
    # The plan is stored under locally resolved authority, never model-chosen scope.
    assert plan["user_uuid"] == "user-1"
    assert plan["workspace_uuid"] == workspace.workspace_uuid
    assert plan["project_uuid"] == project.project_uuid
    assert plan["input_message_uuid"] == query.message_uuid
    assert plan["contract_hash"]

    preflight_execution = connection.execute(
        """
        SELECT model_profile_uuid, provider_type, status
        FROM prompt_execution_runs
        WHERE message_uuid = ? AND prompt_id = 'memorist.preflight_planning'
        """,
        (query.message_uuid,),
    ).fetchone()
    assert preflight_execution is not None
    assert preflight_execution["model_profile_uuid"] == profiles[ModelRole.PREFLIGHT]
    assert preflight_execution["provider_type"] == "openai_compatible_llm"

    # The recall chat is a different session; the evidence has to cross chats.
    assert response.rendered_attachment is not None
    assert "timeout" in str(response.rendered_attachment)
    assert response.attachment_uuid is not None

    # The audit record must keep the contract-required integer rather than
    # rewriting it as "[REDACTED]" because the field name contains "token".
    recorded_plan_items = json.loads(
        connection.execute(
            """
            SELECT validated_output_ijson
            FROM prompt_execution_runs
            WHERE message_uuid = ? AND prompt_id = 'memorist.preflight_planning'
            """,
            (query.message_uuid,),
        ).fetchone()["validated_output_ijson"]
    )["items"]
    assert isinstance(recorded_plan_items[0]["estimated_tokens"], int)

    prompts_seen = [row["prompt_text"] for row in _SemanticProviderHandler.requests]
    assert any("memorist.semantic_candidate_analysis" in text for text in prompts_seen)
    assert any("memorist.preflight_planning" in text for text in prompts_seen)
    # Unresolved template placeholders must never reach a provider.
    assert not any("{{" in text for text in prompts_seen)
    connection.close()


def test_prompt_audit_redaction_keeps_numbers_but_still_hides_credentials() -> None:
    """Redaction must not corrupt the record it is auditing.

    ``_redact_secrets`` matches secret markers as substrings, so contract fields
    like ``estimated_tokens`` and ``max_input_tokens`` match "token". Rewriting
    those as "[REDACTED]" made the stored execution record fail its own contract
    on replay, and — because validation used to run on the redacted copy — turned
    every successful preflight planning call into a recorded error that then
    failed open and discarded the model's retrieval plan.
    """

    from memcore.memory_worker.prompts.registry import _redact_secrets

    redacted = _redact_secrets(
        {
            "estimated_tokens": 819,
            "max_input_tokens": 100000,
            "api_key": "placeholder-provider-credential",
            "nested": {
                "access_token": "bearer-not-a-real-credential",
                "output_tokens": 12,
                "secret_env_var_name": "MEMORIST_PROVIDER_KEY",
                "token_budget": None,
            },
            "items": [{"estimated_tokens": 0, "session_key": "opaque-key-value"}],
        }
    )

    assert redacted["estimated_tokens"] == 819
    assert redacted["max_input_tokens"] == 100000
    assert redacted["nested"]["output_tokens"] == 12
    assert redacted["nested"]["token_budget"] is None
    assert redacted["items"][0]["estimated_tokens"] == 0
    # Anything that could actually carry a credential still goes.
    assert redacted["api_key"] == "[REDACTED]"
    assert redacted["nested"]["access_token"] == "[REDACTED]"
    assert redacted["nested"]["secret_env_var_name"] == "[REDACTED]"
    assert redacted["items"][0]["session_key"] == "[REDACTED]"
