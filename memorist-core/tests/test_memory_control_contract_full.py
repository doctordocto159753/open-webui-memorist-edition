from __future__ import annotations

import importlib
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from fastapi import HTTPException

from memcore.api import routes_memory_control, routes_openwebui, routes_retrieval
from memcore.api.routes_memory_control import AttachmentActionRequest
from memcore.api.routes_openwebui import MessageCaptureRequest, SessionResolveRequest
from memcore.api.routes_retrieval import AssistantResponseCompletedRequest
from memcore.config import Settings
from memcore.graph.falkordb import FalkorDBClient, FalkorDBHealth
from memcore.imports.runtime import initialize_runtime_storage
from memcore.memory_control.full_retrieval import FullPostgresPreflightService
from memcore.memory_control.policy import normalize_turn_policy
from memcore.memory_control.repository import (
    MemoryControlRepository,
    ResolvedTurnPolicy,
)
from memcore.memory_control.runtime import memory_control_connection
from memcore.models import utc_now
from memcore.preflight import PreflightRequest

pytestmark = pytest.mark.skipif(
    not os.getenv("MEMORIST_POSTGRES_DSN"), reason="requires real PostgreSQL"
)


@pytest.fixture
def full_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    settings = Settings(
        env="test",
        allow_legacy_actor_headers_for_tests=True,
        runtime_profile="full",
        canonical_store="postgres",
        postgres_dsn=os.environ["MEMORIST_POSTGRES_DSN"],
        db_path=str(tmp_path / "must-not-exist.sqlite3"),
        object_store_path=str(tmp_path / "objects"),
        graph_backend="falkordb",
        falkordb_url=os.getenv("MEMORIST_FALKORDB_URL", "redis://127.0.0.1:1"),
        allow_full_graph_degraded=True,
        hot_scheduler="in_memory",
    )
    initialize_runtime_storage(settings)
    monkeypatch.setattr(routes_openwebui, "get_settings", lambda: settings)
    monkeypatch.setattr(routes_retrieval, "get_settings", lambda: settings)
    monkeypatch.setattr(routes_memory_control, "get_settings", lambda: settings)
    return settings


def _seed_turn(
    settings: Settings,
    *,
    policy: str = "full",
    with_memory: bool = False,
) -> dict[str, str]:
    suffix = uuid4().hex
    ids = {
        "workspace": f"workspace-{suffix}",
        "project": f"project-{suffix}",
        "session": f"session-{suffix}",
        "message": f"message-{suffix}",
        "user": f"user-{suffix}",
        "chat": f"chat-{suffix}",
        "memory": f"memory-{suffix}",
        "version": f"version-{suffix}",
    }
    now = utc_now()
    with memory_control_connection(settings) as connection:
        connection.execute(
            "INSERT INTO workspaces (workspace_uuid, name, created_at, updated_at) "
            "VALUES (?, ?, ?, ?)",
            (ids["workspace"], suffix, now, now),
        )
        connection.execute(
            "INSERT INTO projects (project_uuid, workspace_uuid, name, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (ids["project"], ids["workspace"], suffix, now, now),
        )
        connection.execute(
            """
            INSERT INTO sessions (
                session_uuid, workspace_uuid, project_uuid, openwebui_conversation_id,
                status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 'active', ?, ?)
            """,
            (ids["session"], ids["workspace"], ids["project"], ids["chat"], now, now),
        )
        connection.execute(
            """
            INSERT INTO messages (
                message_uuid, session_uuid, turn_index, role, creator_type, raw_text,
                processing_status, visibility, is_deleted, created_at, updated_at
            ) VALUES (?, ?, 0, 'user', 'user', ?, 'pending', 'visible', false, ?, ?)
            """,
            (ids["message"], ids["session"], f"Recall postgres graph fact {suffix}", now, now),
        )
        resolved = ResolvedTurnPolicy(
            policy=normalize_turn_policy(policy), source="turn", attachment_review=False
        )
        MemoryControlRepository(connection, "full").record_turn_contract(
            input_message_uuid=ids["message"],
            session_uuid=ids["session"],
            workspace_uuid=ids["workspace"],
            user_uuid=ids["user"],
            chat_uuid=ids["chat"],
            resolved=resolved,
        )
        if with_memory:
            connection.execute(
                """
                INSERT INTO memories (
                    memory_uuid, scope_type, scope_uuid, canonical_key,
                    current_version_uuid, status, created_at, updated_at
                ) VALUES (?, 'workspace', ?, ?, ?, 'active', ?, ?)
                """,
                (
                    ids["memory"],
                    ids["workspace"],
                    f"fact:{suffix}",
                    ids["version"],
                    now,
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO memory_versions (
                    memory_version_uuid, memory_uuid, version_number, operation, value,
                    normalized_text, confidence, importance, source_snapshot_hash,
                    status, created_at
                ) VALUES (?, ?, 1, 'create', ?, ?, 0.95, 0.9, ?, 'current', ?)
                """,
                (
                    ids["version"],
                    ids["memory"],
                    f"Postgres graph fact {suffix}",
                    f"postgres graph fact {suffix}",
                    suffix,
                    now,
                ),
            )
        connection.commit()
    return ids


def _preflight(ids: dict[str, str]) -> PreflightRequest:
    return PreflightRequest(
        session_uuid=ids["session"],
        input_message_uuid=ids["message"],
        token_budget=700,
        turn_policy="full",
        user_uuid=ids["user"],
        workspace_uuid=ids["workspace"],
    )


def _seed_graph_provenance(settings: Settings, ids: dict[str, str]) -> dict[str, object]:
    suffix = uuid4().hex
    source: dict[str, Any] = {
        "processing": f"processing-{suffix}",
        "unit": f"unit-{suffix}",
        "analysis": f"analysis-{suffix}",
        "annotation": f"annotation-{suffix}",
        "route": f"route-{suffix}",
        "candidate": f"candidate-{suffix}",
        "evidence": [f"evidence-a-{suffix}", f"evidence-b-{suffix}"],
    }
    now = utc_now()
    with memory_control_connection(settings) as connection:
        connection.execute(
            "INSERT INTO memory_processing_runs (processing_run_uuid, session_uuid, "
            "message_uuid, pipeline_version, input_content_hash, status, created_at) "
            "VALUES (?, ?, ?, 'test', ?, 'completed', ?)",
            (source["processing"], ids["session"], ids["message"], suffix, now),
        )
        connection.execute(
            """
            INSERT INTO text_units (
                text_unit_uuid, unit_uuid, message_uuid, session_uuid, speaker_role,
                unit_type, unit_index, text, start_char, end_char, content_hash, created_at
            ) VALUES (?, ?, ?, ?, 'user', 'sentence', 0, ?, 0, 20, ?, ?)
            """,
            (
                source["unit"],
                source["unit"],
                ids["message"],
                ids["session"],
                "postgres graph fact",
                suffix,
                now,
            ),
        )
        connection.execute(
            """
            INSERT INTO jakobson_analysis_runs (
                analysis_run_uuid, workspace_uuid, project_uuid, session_uuid, message_uuid,
                prompt_id, prompt_version, provider_type, input_hash, status, created_at
            ) VALUES (?, ?, ?, ?, ?, 'test', '1', 'test', ?, 'completed', ?)
            """,
            (
                source["analysis"],
                ids["workspace"],
                ids["project"],
                ids["session"],
                ids["message"],
                suffix,
                now,
            ),
        )
        connection.execute(
            """
            INSERT INTO jakobson_sentence_annotations (
                annotation_uuid, analysis_run_uuid, message_uuid, unit_uuid,
                sentence_index, sentence_text, sentence_hash, sender_confidence,
                receiver_confidence, message_confidence, context_confidence,
                code_confidence, contact_channel_confidence, dominant_function,
                raw_sentence_output_jsonb, created_at
            ) VALUES (?, ?, ?, ?, 0, 'postgres graph fact', ?, 'high', 'high', 'high',
                      'high', 'high', 'high', 'referential', '{}'::jsonb, ?)
            """,
            (
                source["annotation"],
                source["analysis"],
                ids["message"],
                source["unit"],
                suffix,
                now,
            ),
        )
        connection.execute(
            """
            INSERT INTO memory_signal_routes (
                route_uuid, annotation_uuid, message_uuid, unit_uuid, dominant_function,
                route_type, extractor_id, priority, confidence, reason, status, created_at
            ) VALUES (?, ?, ?, ?, 'referential', 'fact', 'test', 100, 'high',
                      'test provenance', 'ready', ?)
            """,
            (
                source["route"],
                source["annotation"],
                ids["message"],
                source["unit"],
                now,
            ),
        )
        connection.execute(
            """
            INSERT INTO memory_candidates (
                candidate_uuid, processing_run_uuid, text_unit_uuid, candidate_type,
                subject_key, predicate, object_jsonb, normalized_text, source_authority,
                explicitness, confidence, importance, status, created_at
            ) VALUES (?, ?, ?, 'fact', 'subject', 'predicate', '{}'::jsonb,
                      'postgres graph fact', 'user', 'explicit', 0.95, 0.9,
                      'accepted', ?)
            """,
            (source["candidate"], source["processing"], source["unit"], now),
        )
        for index, evidence_uuid in enumerate(source["evidence"]):
            connection.execute(
                """
                INSERT INTO candidate_evidence (
                    evidence_uuid, candidate_uuid, message_uuid, text_unit_uuid,
                    annotation_uuid, route_uuid, evidence_text, start_char, end_char,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, 20, ?)
                """,
                (
                    evidence_uuid,
                    source["candidate"],
                    ids["message"],
                    source["unit"],
                    source["annotation"],
                    source["route"],
                    f"source evidence {index}",
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO memory_evidence_links (
                    link_uuid, memory_uuid, memory_version_uuid, candidate_uuid,
                    evidence_uuid, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    f"link-{index}-{suffix}",
                    ids["memory"],
                    ids["version"],
                    source["candidate"],
                    evidence_uuid,
                    now,
                ),
            )
        connection.commit()
    return source


def test_full_private_creates_no_canonical_or_graph_trace(
    full_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    before: dict[str, int] = {}
    tables = (
        "sessions",
        "messages",
        "jobs",
        "retrieval_runs",
        "memory_context_attachments",
        "memorist_policy_audit_events",
    )
    with memory_control_connection(full_settings) as connection:
        for table in tables:
            before[table] = int(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0])

    def graph_forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("Private turn must not query FalkorDB")

    monkeypatch.setattr(
        "memcore.graph.falkordb.FalkorDBClient.query_memory_versions", graph_forbidden
    )
    session = routes_openwebui.resolve_session(
        SessionResolveRequest(
            openwebui_conversation_id=f"private-{uuid4().hex}",
            user_id="private-user",
            turn_policy="private",
        )
    )
    captured = routes_openwebui.capture_message(
        MessageCaptureRequest(role="user", content="never persist this", turn_policy="private")
    )
    assert session["session_uuid"] is None
    assert captured["message_uuid"] is None
    with memory_control_connection(full_settings) as connection:
        after = {
            table: int(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
            for table in tables
        }
    assert after == before
    assert not Path(full_settings.db_path).exists()


def test_full_concurrent_callbacks_are_single_delivery_and_single_capture(
    full_settings: Settings,
) -> None:
    ids = _seed_turn(full_settings)
    capture_request = MessageCaptureRequest(
        session_uuid=ids["session"],
        openwebui_conversation_id=ids["chat"],
        user_id=ids["user"],
        workspace_uuid=ids["workspace"],
        role="user",
        content="concurrent callback",
        idempotency_key=f"capture-{uuid4().hex}",
        turn_policy="full",
    )
    with ThreadPoolExecutor(max_workers=2) as executor:
        captures = list(
            executor.map(lambda _index: routes_openwebui.capture_message(capture_request), range(2))
        )
    assert sorted(result["duplicate"] for result in captures) == [False, True]
    assert len({result["message_uuid"] for result in captures}) == 1

    attachment_uuid = f"attachment-{uuid4().hex}"
    with memory_control_connection(full_settings) as connection:
        connection.execute(
            """
            INSERT INTO memory_context_attachments (
                attachment_uuid, session_uuid, project_uuid, input_message_uuid,
                attachment_mode, ijson_attachment, rendered_attachment, token_count,
                owner_user_uuid, workspace_uuid, lifecycle_status, attachment_review,
                generation, created_at, schema_version
            ) VALUES (?, ?, ?, ?, 'full', '{}', 'bounded', 1, ?, ?, 'prepared',
                      false, 1, ?, 1)
            """,
            (
                attachment_uuid,
                ids["session"],
                ids["project"],
                ids["message"],
                ids["user"],
                ids["workspace"],
                utc_now(),
            ),
        )
        connection.commit()

    headers = {
        "x_memorist_user_id": ids["user"],
        "x_memorist_workspace_id": ids["workspace"],
    }

    def deliver(index: int) -> dict[str, object]:
        return routes_memory_control.record_attachment_delivery(
            attachment_uuid,
            AttachmentActionRequest(idempotency_key=f"concurrent-delivery-{index}-{uuid4().hex}"),
            **headers,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        deliveries = list(executor.map(deliver, range(2)))
    assert len({result["lifecycle_event_uuid"] for result in deliveries}) == 1
    with memory_control_connection(full_settings) as connection:
        count = connection.execute(
            """
            SELECT count(*) FROM memorist_attachment_lifecycle_events
            WHERE attachment_uuid = ? AND lifecycle_status = 'delivered'
            """,
            (attachment_uuid,),
        ).fetchone()[0]
    assert count == 1


def test_full_openwebui_filter_inlet_outlet_use_postgres(
    full_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    integration_root = Path(__file__).resolve().parents[2] / "open-webui-integration" / "memorist"
    if str(integration_root) not in sys.path:
        sys.path.insert(0, str(integration_root))
    filter_module = importlib.import_module("filter.memorist_memory_filter")
    schemas = importlib.import_module("shared.schemas")

    class PostgresRouteClient:
        def __init__(self, _config: object) -> None:
            pass

        def resolve_turn_policy(self, **kwargs: Any) -> object:
            raw_control = kwargs.get("request_control")
            control: dict[str, Any] = raw_control if isinstance(raw_control, dict) else {}
            mode = str(control.get("turn_policy") or "full")
            policy = normalize_turn_policy(mode)
            return schemas.ResolvedTurnPolicy(
                mode=mode,
                capture_enabled=policy.capture_enabled,
                recall_enabled=policy.recall_enabled,
                attachment_enabled=policy.attachment_enabled,
                private=policy.private,
                source="turn",
                attachment_review=bool(control.get("attachment_review", False)),
                runtime_profile="full",
            )

        def resolve_session(self, **kwargs: Any) -> object:
            result = routes_openwebui.resolve_session(SessionResolveRequest(**kwargs))
            return schemas.ResolvedSession(
                result["session_uuid"], result["workspace_uuid"], result["project_uuid"]
            )

        def capture_message(
            self, session_uuid: str, role: str, content: str, **kwargs: Any
        ) -> object:
            result = routes_openwebui.capture_message(
                MessageCaptureRequest(
                    session_uuid=session_uuid,
                    role=role,
                    content=content,
                    **kwargs,
                )
            )
            suffix = uuid4().hex
            with memory_control_connection(full_settings) as connection:
                session = connection.execute(
                    "SELECT workspace_uuid FROM sessions WHERE session_uuid = ?",
                    (session_uuid,),
                ).fetchone()
                memory_uuid = f"filter-memory-{suffix}"
                version_uuid = f"filter-version-{suffix}"
                now = utc_now()
                connection.execute(
                    """
                    INSERT INTO memories (
                        memory_uuid, scope_type, scope_uuid, canonical_key,
                        current_version_uuid, status, created_at, updated_at
                    ) VALUES (?, 'workspace', ?, ?, ?, 'active', ?, ?)
                    """,
                    (
                        memory_uuid,
                        session["workspace_uuid"],
                        memory_uuid,
                        version_uuid,
                        now,
                        now,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO memory_versions (
                        memory_version_uuid, memory_uuid, version_number, operation,
                        value, normalized_text, confidence, importance,
                        source_snapshot_hash, status, created_at
                    ) VALUES (?, ?, 1, 'create', ?, ?, 0.9, 0.9, ?, 'current', ?)
                    """,
                    (version_uuid, memory_uuid, content, content, suffix, now),
                )
                connection.commit()
            return schemas.CapturedMessage(
                result["session_uuid"], result["message_uuid"], result["duplicate"]
            )

        def preflight(self, session_uuid: str, input_message_uuid: str, **kwargs: Any) -> object:
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
        ) -> object:
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
        ) -> object:
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

    monkeypatch.setattr(filter_module, "MemoristClient", PostgresRouteClient)
    monkeypatch.setattr(
        "memcore.memory_control.full_retrieval.FalkorDBClient.health",
        lambda *_args, **_kwargs: FalkorDBHealth(
            ok=False, status="degraded", error_sanitized="filter-test-outage"
        ),
    )
    filter_instance = filter_module.Filter()
    inlet_body = {
        "conversation_id": f"filter-chat-{uuid4().hex}",
        "memorist": {"turn_policy": "full"},
        "messages": [{"role": "user", "id": "user-message", "content": "filter postgres fact"}],
    }
    actor = {"id": "filter-user", "workspace_id": "trusted-openwebui-workspace"}
    inlet = filter_instance.inlet(inlet_body, actor)
    assert inlet["metadata"]["memorist_runtime_profile"] == "full"
    assert inlet["metadata"]["memorist_delivered_attachment_uuid"]
    outlet = filter_instance.outlet(
        {
            "id": "filter-provider-response",
            "metadata": inlet["metadata"],
            "messages": [{"role": "assistant", "content": "filter assistant answer"}],
        },
        actor,
    )
    assert "memorist_last_error" not in outlet["metadata"]
    with memory_control_connection(full_settings) as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM messages WHERE raw_text = 'filter assistant answer'"
            ).fetchone()[0]
            == 1
        )
        extraction_jobs = connection.execute(
            "SELECT payload_ijson FROM jobs WHERE job_type = 'memory_extraction' "
            "AND idempotency_key LIKE 'openwebui:memory_extraction:%'"
        ).fetchall()
        assert len(extraction_jobs) == 2
        for row in extraction_jobs:
            payload = json.loads(row["payload_ijson"])
            assert payload["model_role"] == "memory_extraction"
            assert "model_profile_uuid" in payload
            assert payload["provider_type"] == "deterministic"
            assert payload["model_name"] == "deterministic_extraction"
        usage = connection.execute(
            "SELECT role, stage, provider_type, model_name FROM model_usage_events "
            "WHERE stage = 'memory_extraction_queued'"
        ).fetchall()
        assert len(usage) == 2
        assert all(row["role"] == "memory_extraction" for row in usage)
    assert not Path(full_settings.db_path).exists()


def test_full_no_recall_captures_but_skips_retrieval(full_settings: Settings) -> None:
    ids = _seed_turn(full_settings, policy="no_recall")
    response = routes_retrieval.run_preflight(_preflight(ids))
    assert response["status"] == "disabled"
    completed = routes_retrieval.assistant_response_completed(
        AssistantResponseCompletedRequest(
            input_message_uuid=ids["message"],
            assistant_text="Captured assistant response",
            turn_policy="no_recall",
            user_uuid=ids["user"],
            workspace_uuid=ids["workspace"],
        )
    )
    with memory_control_connection(full_settings) as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM retrieval_runs WHERE input_message_uuid = ?",
                (ids["message"],),
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute(
                "SELECT count(*) FROM memory_context_attachments WHERE input_message_uuid = ?",
                (ids["message"],),
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute(
                "SELECT count(*) FROM jobs WHERE payload_ijson LIKE ?",
                (f"%{completed['assistant_message_uuid']}%",),
            ).fetchone()[0]
            == 2
        )
    assert not Path(full_settings.db_path).exists()


def test_full_graph_retrieval_persists_postgres_provenance_and_restart(
    full_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    ids = _seed_turn(full_settings, with_memory=True)
    graph_source = _seed_graph_provenance(full_settings, ids)
    with memory_control_connection(full_settings) as connection:
        block_uuid = f"block-{uuid4().hex}"
        block_version_uuid = f"block-version-{uuid4().hex}"
        now = utc_now()
        connection.execute(
            """
            INSERT INTO memory_blocks (
                block_uuid, scope_type, scope_uuid, block_type, canonical_key,
                current_version_uuid, status, created_at, updated_at
            ) VALUES (?, 'workspace', ?, 'working_context', ?, ?, 'active', ?, ?)
            """,
            (block_uuid, ids["workspace"], block_uuid, block_version_uuid, now, now),
        )
        connection.execute(
            """
            INSERT INTO memory_block_versions (
                block_version_uuid, block_uuid, version_number, value,
                source_snapshot_hash, created_at
            ) VALUES (?, ?, 1, 'active block contract text', ?, ?)
            """,
            (block_version_uuid, block_uuid, uuid4().hex, now),
        )
        connection.commit()
    if os.getenv("MEMORIST_REAL_FALKORDB") == "1":
        graph = FalkorDBClient(full_settings.falkordb_url or "")
        assert graph.health().ok
        graph.graph_query(
            "CREATE "
            f"(w:Workspace {{uuid:'{ids['workspace']}'}})-[:IN_PROJECT]->"
            "(p:Project)-[:HAS_SESSION]->(s:Session)-[:HAS_MESSAGE]->"
            "(msg:Message)-[:HAS_UNIT]->(unit:TextUnit)-[:HAS_JAKOBSON_ANNOTATION]->"
            f"(ann:JakobsonAnnotation)-[:ROUTES_TO]->(route:MemorySignalRoute "
            f"{{uuid:'{graph_source['route']}'}})-[:DERIVED_FROM]->"
            f"(candidate:MemoryCandidate {{uuid:'{graph_source['candidate']}'}}), "
            f"(mem:Memory {{uuid:'{ids['memory']}', status:'active'}})-[:HAS_VERSION]->"
            f"(ver:MemoryVersion {{uuid:'{ids['version']}', status:'current', "
            f"text:'postgres graph fact {ids['version']}'}}), "
            "(ver)-[:EVIDENCED_BY]->(candidate) RETURN ver.uuid"
        )
    else:
        monkeypatch.setattr(
            "memcore.memory_control.full_retrieval.FalkorDBClient.health",
            lambda *_args, **_kwargs: FalkorDBHealth(ok=True, status="ok"),
        )
        monkeypatch.setattr(
            "memcore.memory_control.full_retrieval.FalkorDBClient.query_memory_versions",
            lambda *_args, **_kwargs: [
                {
                    "memory_uuid": ids["memory"],
                    "memory_version_uuid": ids["version"],
                    "memory_signal_route_uuid": graph_source["route"],
                    "memory_candidate_uuid": graph_source["candidate"],
                }
            ],
        )
    response = routes_retrieval.run_preflight(_preflight(ids))
    assert response["status"] == "attached"
    assert response["graph_status"] == "healthy"
    assert "active block contract text" in response["rendered_attachment"]
    attachment_uuid = str(response["attachment_uuid"])
    headers = {
        "x_memorist_user_id": ids["user"],
        "x_memorist_workspace_id": ids["workspace"],
    }
    with pytest.raises(HTTPException) as denied:
        routes_memory_control.preview_attachment(
            attachment_uuid,
            x_memorist_user_id="substitution-attacker",
            x_memorist_workspace_id=ids["workspace"],
        )
    assert denied.value.status_code == 404
    delivered = routes_memory_control.record_attachment_delivery(
        attachment_uuid,
        AttachmentActionRequest(idempotency_key=f"delivery-{uuid4().hex}"),
        **headers,
    )
    duplicate = routes_memory_control.record_attachment_delivery(
        attachment_uuid,
        AttachmentActionRequest(idempotency_key=f"delivery-{uuid4().hex}"),
        **headers,
    )
    assert duplicate["lifecycle_event_uuid"] == delivered["lifecycle_event_uuid"]
    with memory_control_connection(full_settings) as restarted:
        attachment = MemoryControlRepository(restarted, "full").attachment_for_actor(
            attachment_uuid,
            user_uuid=ids["user"],
            workspace_uuid=ids["workspace"],
        )
        sources = restarted.execute(
            "SELECT source_type, memory_version_uuid FROM memorist_retrieval_sources "
            "WHERE attachment_uuid = ?",
            (attachment_uuid,),
        ).fetchall()
    assert attachment is not None
    assert attachment["lifecycle_status"] == "delivered"
    assert {row["source_type"] for row in sources} == {
        "memory_version",
        "memory_signal_route",
        "memory_candidate",
        "source_evidence",
        "active_block",
    }
    assert {
        row["memory_version_uuid"] for row in sources if row["memory_version_uuid"] is not None
    } == {ids["version"]}
    assert sum(row["source_type"] == "source_evidence" for row in sources) == 2
    assert not Path(full_settings.db_path).exists()


def test_full_regeneration_preserves_attachment_provenance(
    full_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    ids = _seed_turn(full_settings, with_memory=True)
    monkeypatch.setattr(
        "memcore.memory_control.full_retrieval.FalkorDBClient.health",
        lambda *_args, **_kwargs: FalkorDBHealth(
            ok=False, status="degraded", error_sanitized="planned-test-outage"
        ),
    )
    response = routes_retrieval.run_preflight(_preflight(ids))
    attachment_uuid = str(response["attachment_uuid"])
    headers = {
        "x_memorist_user_id": ids["user"],
        "x_memorist_workspace_id": ids["workspace"],
    }
    routes_memory_control.record_attachment_delivery(
        attachment_uuid,
        AttachmentActionRequest(idempotency_key=f"delivery-{uuid4().hex}"),
        **headers,
    )
    original_response = routes_retrieval.assistant_response_completed(
        AssistantResponseCompletedRequest(
            input_message_uuid=ids["message"],
            assistant_text="Original response with Memorist context",
            attachment_uuid=attachment_uuid,
            provider_response_id=f"original-{uuid4().hex}",
            user_uuid=ids["user"],
            workspace_uuid=ids["workspace"],
        )
    )
    rejection_key = f"post-use-rejection-{uuid4().hex}"
    first_rejection = routes_memory_control.record_attachment_rejection(
        attachment_uuid,
        AttachmentActionRequest(idempotency_key=rejection_key),
        **headers,
    )
    repeated_rejection = routes_memory_control.record_attachment_rejection(
        attachment_uuid,
        AttachmentActionRequest(idempotency_key=rejection_key),
        **headers,
    )
    assert first_rejection["lifecycle_event_uuid"] == repeated_rejection["lifecycle_event_uuid"]
    regeneration = routes_memory_control.regenerate_without_recall(
        attachment_uuid,
        AttachmentActionRequest(
            idempotency_key=f"regenerate-{uuid4().hex}",
            response_message_uuid=str(original_response["assistant_message_uuid"]),
        ),
        **headers,
    )
    assert regeneration["turn_policy"] == "no_recall"
    assert regeneration["original_user_prompt"].startswith("Recall postgres graph fact")

    def complete(index: int) -> dict[str, Any]:
        return routes_retrieval.assistant_response_completed(
            AssistantResponseCompletedRequest(
                input_message_uuid=ids["message"],
                assistant_text=f"Regenerated without recall variant {index}",
                provider_response_id=f"regenerated-provider-{index}-{uuid4().hex}",
                regeneration_uuid=str(regeneration["regeneration_uuid"]),
                turn_policy="no_recall",
                user_uuid=ids["user"],
                workspace_uuid=ids["workspace"],
            )
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        completions = list(pool.map(complete, range(2)))
    assert len({item["assistant_message_uuid"] for item in completions}) == 1
    assert len({item["response_link_uuid"] for item in completions}) == 1
    completed = completions[0]
    with memory_control_connection(full_settings) as connection:
        attachment = connection.execute(
            "SELECT lifecycle_status, user_disposition FROM memory_context_attachments "
            "WHERE attachment_uuid = ?",
            (attachment_uuid,),
        ).fetchone()
        regeneration_row = connection.execute(
            "SELECT * FROM memorist_regenerations WHERE regeneration_uuid = ?",
            (regeneration["regeneration_uuid"],),
        ).fetchone()
        attachment_count = connection.execute(
            "SELECT count(*) FROM memory_context_attachments WHERE input_message_uuid = ?",
            (ids["message"],),
        ).fetchone()[0]
    assert attachment["lifecycle_status"] == "used_for_response"
    assert attachment["user_disposition"] == "rejected"
    assert regeneration_row["status"] == "completed"
    assert (
        regeneration_row["regenerated_assistant_message_uuid"]
        == completed["assistant_message_uuid"]
    )
    assert attachment_count == 1


def test_full_graph_outage_is_explicit_and_never_changes_store(
    full_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    ids = _seed_turn(full_settings, with_memory=True)
    monkeypatch.setattr(
        "memcore.memory_control.full_retrieval.FalkorDBClient.health",
        lambda *_args, **_kwargs: FalkorDBHealth(
            ok=False, status="degraded", error_sanitized="falkordb_unavailable"
        ),
    )
    with memory_control_connection(full_settings) as connection:
        response = FullPostgresPreflightService(connection, full_settings).run(
            _preflight(ids), normalize_turn_policy("full"), attachment_review=False
        )
    assert response.graph_status == "degraded"
    assert response.degraded_reason == "falkordb_unavailable"
    with memory_control_connection(full_settings) as connection:
        run = connection.execute(
            "SELECT graph_status, degraded_reason FROM retrieval_runs WHERE retrieval_run_uuid = ?",
            (response.retrieval_run_uuid,),
        ).fetchone()
        audit = connection.execute(
            "SELECT degraded_reason FROM memorist_policy_audit_events "
            "WHERE input_message_uuid = ? AND event_type = 'full_retrieval_completed'",
            (ids["message"],),
        ).fetchone()
    assert run["graph_status"] == "degraded"
    assert run["degraded_reason"] == "falkordb_unavailable"
    assert audit["degraded_reason"] == "falkordb_unavailable"
    assert not Path(full_settings.db_path).exists()


def test_full_disabled_or_missing_graph_configuration_opens_no_socket(
    full_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = 0

    def unexpected_socket(*_args: object, **_kwargs: object) -> FalkorDBHealth:
        nonlocal calls
        calls += 1
        raise AssertionError("graph socket must not be opened")

    monkeypatch.setattr(FalkorDBClient, "health", unexpected_socket)
    scope = {"workspace_uuid": "workspace", "raw_text": "query"}
    with memory_control_connection(full_settings) as connection:
        disabled = full_settings.model_copy(update={"graph_backend": "disabled"})
        rows, status, reason = FullPostgresPreflightService(connection, disabled)._graph_candidates(
            scope
        )
        assert (rows, status, reason) == ([], "degraded", "graph_backend_disabled")
        missing = full_settings.model_copy(update={"falkordb_url": None})
        rows, status, reason = FullPostgresPreflightService(connection, missing)._graph_candidates(
            scope
        )
        assert (rows, status, reason) == ([], "degraded", "falkordb_url_missing")
    assert calls == 0


def test_full_rejects_unverified_graph_source_ids(
    full_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    ids = _seed_turn(full_settings, with_memory=True)
    monkeypatch.setattr(
        "memcore.memory_control.full_retrieval.FalkorDBClient.health",
        lambda *_args, **_kwargs: FalkorDBHealth(ok=True, status="ok"),
    )
    monkeypatch.setattr(
        "memcore.memory_control.full_retrieval.FalkorDBClient.query_memory_versions",
        lambda *_args, **_kwargs: [
            {
                "memory_uuid": ids["memory"],
                "memory_version_uuid": ids["version"],
                "memory_signal_route_uuid": "client-injected-route",
                "memory_candidate_uuid": "client-injected-candidate",
            }
        ],
    )

    response = routes_retrieval.run_preflight(_preflight(ids))

    with memory_control_connection(full_settings) as connection:
        sources = connection.execute(
            "SELECT source_type, source_uuid FROM memorist_retrieval_sources "
            "WHERE attachment_uuid = ?",
            (response["attachment_uuid"],),
        ).fetchall()
    assert {row["source_type"] for row in sources} == {"memory_version"}
    assert not any("client-injected" in row["source_uuid"] for row in sources)
