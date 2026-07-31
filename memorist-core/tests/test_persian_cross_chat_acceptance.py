"""Persian cross-chat acceptance through the real Open WebUI filter path.

This is the acceptance flow, not a focused unit test. It drives the shipped
``Filter.inlet``/``Filter.outlet`` against the real Core route handlers, runs the
real memory worker, then opens a *second* Open WebUI conversation resolving to a
different session and asks a paraphrased question. Nothing here seeds semantic
rows, hands the retriever a query plan, or repeats the source sentence verbatim.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from memcore.api import routes_memory_control, routes_openwebui, routes_retrieval
from memcore.api.routes_memory_control import AttachmentActionRequest
from memcore.api.routes_openwebui import MessageCaptureRequest, SessionResolveRequest
from memcore.api.routes_retrieval import AssistantResponseCompletedRequest
from memcore.config import Settings
from memcore.memory_control.policy import normalize_turn_policy
from memcore.memory_control.runtime import memory_control_connection
from memcore.memory_worker.pipeline import MemoryWorkerPipeline
from memcore.preflight import PreflightRequest
from memcore.storage.migrations import apply_migrations
from memcore.storage.sqlite import connect

pytestmark = pytest.mark.usefixtures("wp02_downstream_semantic_model")

# The durable decision the user states once, in chat A.
DECISION = (
    "برای پروژه Memorist تصمیم قطعی من این است که timeout هر نقش پردازشی باید "
    "جداگانه و از UI و فایل env قابل تنظیم باشد. این را به‌عنوان تصمیم پایدار "
    "پروژه به خاطر بسپار."
)
# The question asked later, in chat B. Deliberately paraphrased: it shares no
# distinctive multi-word phrase with the decision above, so an exact-substring
# match cannot satisfy this test.
FOLLOW_UP = "پیکربندی مهلت اجرای نقش‌ها در این پروژه بر چه پایه‌ای نهایی شد؟"
ASSISTANT_ANSWER = "بر پایهٔ تنظیم جداگانه برای هر نقش، از رابط کاربری و فایل محیطی."

USER = "acceptance-user"
WORKSPACE = "123e4567-e89b-42d3-a456-426614174111"


@pytest.fixture()
def lite_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    db_path = tmp_path / "acceptance.sqlite"
    connection = connect(db_path)
    apply_migrations(connection)
    connection.close()
    settings = Settings(
        env="test",
        allow_legacy_actor_headers_for_tests=True,
        db_path=str(db_path),
        object_store_path=str(tmp_path / "objects"),
        graph_backend="disabled",
    )
    for module in (routes_openwebui, routes_retrieval, routes_memory_control):
        monkeypatch.setattr(module, "get_settings", lambda: settings)
    return settings


def _route_client(schemas: Any, outcomes: list[dict[str, Any]]) -> type:
    """A transport-only stand-in that calls the real Core route handlers."""

    class LiteRouteClient:
        def __init__(self, _config: object) -> None:
            pass

        def record_integration_outcome(self, **kwargs: Any) -> dict[str, Any]:
            outcomes.append(dict(kwargs))
            return {"status": "recorded"}

        def resolve_turn_policy(self, **kwargs: Any) -> Any:
            control = kwargs.get("request_control") or {}
            mode = str(control.get("turn_policy") or "full")
            policy = normalize_turn_policy(mode)
            return schemas.ResolvedTurnPolicy(
                mode=mode,
                capture_enabled=policy.capture_enabled,
                recall_enabled=policy.recall_enabled,
                attachment_enabled=policy.attachment_enabled,
                private=policy.private,
                source="turn",
                attachment_review=False,
                runtime_profile="lite",
            )

        def resolve_session(self, **kwargs: Any) -> Any:
            result = routes_openwebui.resolve_session(SessionResolveRequest(**kwargs))
            return schemas.ResolvedSession(
                result["session_uuid"], result["workspace_uuid"], result["project_uuid"]
            )

        def capture_message(self, session_uuid: str, role: str, content: str, **kwargs: Any) -> Any:
            result = routes_openwebui.capture_message(
                MessageCaptureRequest(
                    session_uuid=session_uuid, role=role, content=content, **kwargs
                )
            )
            return schemas.CapturedMessage(
                result["session_uuid"], result["message_uuid"], result["duplicate"]
            )

        def preflight(self, session_uuid: str, input_message_uuid: str, **kwargs: Any) -> Any:
            user_uuid = kwargs.pop("user_id", None)
            result = routes_retrieval.run_preflight(
                PreflightRequest(
                    session_uuid=session_uuid,
                    input_message_uuid=input_message_uuid,
                    user_uuid=str(user_uuid) if user_uuid is not None else None,
                    **kwargs,
                )
            )
            return schemas.PreflightResult(
                **{
                    key: result[key]
                    for key in schemas.PreflightResult.__dataclass_fields__
                    if key in result
                }
            )

        def record_attachment_delivery(
            self,
            attachment_uuid: str,
            *,
            user_id: str,
            workspace_uuid: str | None,
            idempotency_key: str,
        ) -> Any:
            return routes_memory_control.record_attachment_delivery(
                attachment_uuid,
                AttachmentActionRequest(idempotency_key=idempotency_key),
                x_memorist_user_id=user_id,
                x_memorist_workspace_id=workspace_uuid,
            )

        def assistant_completed(
            self,
            input_message_uuid: str,
            assistant_text: str,
            attachment_uuid: str | None,
            provider_response_id: str | None,
            **kwargs: Any,
        ) -> Any:
            user_uuid = kwargs.pop("user_id", None)
            return routes_retrieval.assistant_response_completed(
                AssistantResponseCompletedRequest(
                    input_message_uuid=input_message_uuid,
                    assistant_text=assistant_text,
                    attachment_uuid=attachment_uuid,
                    provider_response_id=provider_response_id,
                    user_uuid=str(user_uuid) if user_uuid is not None else None,
                    **kwargs,
                )
            )

    return LiteRouteClient


def test_persian_durable_decision_crosses_chats_through_the_real_filter(
    lite_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    integration_root = Path(__file__).resolve().parents[2] / "open-webui-integration" / "memorist"
    if str(integration_root) not in sys.path:
        sys.path.insert(0, str(integration_root))
    filter_module = importlib.import_module("filter.memorist_memory_filter")
    schemas = importlib.import_module("shared.schemas")
    outcomes: list[dict[str, Any]] = []
    monkeypatch.setattr(filter_module, "MemoristClient", _route_client(schemas, outcomes))

    actor = {"id": USER, "workspace_id": WORKSPACE}
    source_chat = f"chat-source-{uuid4().hex}"
    recall_chat = f"chat-recall-{uuid4().hex}"

    # ---- Chat A: the user states the durable decision ------------------------
    source_body = filter_module.Filter().inlet(
        {
            "conversation_id": source_chat,
            "memorist": {"turn_policy": "full"},
            "messages": [{"role": "user", "id": "m-source", "content": DECISION}],
        },
        actor,
    )
    source_message_uuid = source_body["metadata"]["memorist_input_message_uuid"]
    source_session_uuid = source_body["metadata"]["memorist_session_uuid"]
    assert source_body["messages"][-1]["content"] == DECISION

    # ---- The worker runs over the captured message ---------------------------
    with memory_control_connection(lite_settings) as connection:
        processed = MemoryWorkerPipeline(connection, lite_settings).process_message(
            source_message_uuid
        )
        connection.commit()
    assert processed["semantic_prompt_execution_uuid"] is not None
    assert processed["semantic_terminal_gate_short_circuit"] is False
    assert processed["semantic_coverage_status"] == "complete"
    with memory_control_connection(lite_settings) as connection:
        dispositions = [
            row["disposition"]
            for row in connection.execute(
                """
                SELECT item.disposition
                FROM semantic_coverage_items item
                JOIN semantic_coverage_runs run
                  ON run.coverage_run_uuid = item.coverage_run_uuid
                WHERE run.processing_run_uuid = ?
                """,
                (processed["processing_run_uuid"],),
            ).fetchall()
        ]
    assert dispositions, processed

    # ---- Chat B: a different conversation, resolving to a different session ---
    recall_body = filter_module.Filter().inlet(
        {
            "conversation_id": recall_chat,
            "memorist": {"turn_policy": "full"},
            "messages": [{"role": "user", "id": "m-recall", "content": FOLLOW_UP}],
        },
        actor,
    )
    recall_session_uuid = recall_body["metadata"]["memorist_session_uuid"]
    assert recall_session_uuid != source_session_uuid, (
        "cross-chat recall must not be tested through the same hidden session"
    )

    # The original prompt is untouched and the context is a separate message.
    assert recall_body["messages"][-1]["content"] == FOLLOW_UP
    context_messages = [
        message for message in recall_body["messages"] if message.get("name") == "memorist_context"
    ]
    assert len(context_messages) == 1, recall_body["messages"]
    attachment_text = str(context_messages[0]["content"])
    delivered_attachment_uuid = recall_body["metadata"]["memorist_delivered_attachment_uuid"]
    assert delivered_attachment_uuid

    # The delivered context must carry the chat-A decision, and its provenance
    # must resolve to the chat-A message rather than to anything created in
    # chat B. Both are required: the text alone could come from anywhere, and a
    # provenance ID alone would not prove the decision was actually delivered.
    assert source_message_uuid in attachment_text, attachment_text
    assert "env" in attachment_text and "timeout" in attachment_text, attachment_text
    assert FOLLOW_UP not in attachment_text
    with memory_control_connection(lite_settings) as connection:
        stored = connection.execute(
            "SELECT rendered_attachment, input_message_uuid, retrieval_run_uuid "
            "FROM memory_context_attachments WHERE attachment_uuid = ?",
            (delivered_attachment_uuid,),
        ).fetchone()
        assert stored is not None
        assert (
            stored["input_message_uuid"] == recall_body["metadata"]["memorist_input_message_uuid"]
        )
        plan = connection.execute(
            "SELECT workspace_uuid, input_message_uuid FROM model_retrieval_plans "
            "WHERE retrieval_run_uuid = ?",
            (stored["retrieval_run_uuid"],),
        ).fetchone()
        assert plan is not None, "the accepted retrieval plan was not persisted for this turn"
        # "Memory used" must be backed by a real delivery record, not by the
        # filter's own metadata echo.
        delivered = connection.execute(
            "SELECT COUNT(*) FROM memory_delivery_events WHERE attachment_uuid = ?",
            (delivered_attachment_uuid,),
        ).fetchone()[0]
        assert delivered >= 1

    # ---- The assistant answers; the outlet captures it -----------------------
    outlet_body = filter_module.Filter().outlet(
        {
            "id": f"provider-{uuid4().hex}",
            "metadata": recall_body["metadata"],
            "messages": [{"role": "assistant", "content": ASSISTANT_ANSWER}],
        },
        actor,
    )
    assert "memorist_last_error" not in outlet_body["metadata"]

    with memory_control_connection(lite_settings) as connection:
        assistant = connection.execute(
            "SELECT message_uuid, role, creator_type, session_uuid FROM messages "
            "WHERE raw_text = ? AND session_uuid = ?",
            (ASSISTANT_ANSWER, recall_session_uuid),
        ).fetchall()
        assert len(assistant) == 1
        assert assistant[0]["role"] == "assistant"
        versions = connection.execute(
            "SELECT COUNT(*) FROM message_versions WHERE message_uuid = ?",
            (assistant[0]["message_uuid"],),
        ).fetchone()[0]
        assert versions == 1

    # The whole flow is audited end to end, including the cross-chat recall.
    assert [(row["stage"], row["outcome"]) for row in outcomes] == [
        ("capture", "ok"),
        ("recall", "ok"),
        ("capture", "ok"),
        ("recall", "ok"),
        ("chat_outlet", "ok"),
    ]
