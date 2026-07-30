from __future__ import annotations

import json
import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from memcore.config import Settings
from memcore.main import create_app
from memcore.memory_control.policy import normalize_turn_policy
from memcore.memory_control.repository import MemoryControlRepository, ResolvedTurnPolicy
from memcore.memory_worker.pipeline import MemoryWorkerPipeline
from memcore.memory_worker.prompts.contracts import (
    canonical_jakobson_v3_example,
    canonical_semantic_candidate_v1_example,
)
from memcore.model_control.providers.openai_compatible import (
    OpenAICompatibleEmbeddingProvider,
    OpenAICompatibleLLMProvider,
)
from memcore.model_control.repository import ModelControlRepository
from memcore.model_control.role_contracts import role_contract_manifest
from memcore.model_control.runtime_contracts import runtime_contract_for_role
from memcore.models import ModelRole
from memcore.storage.migrations import apply_migrations
from memcore.storage.sqlite import connect
from memcore.validators.ijson import load_ijson

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def client_and_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[TestClient, Path]:
    db_path = tmp_path / "memorist.sqlite"
    monkeypatch.setenv("MEMORIST_DB_PATH", str(db_path))
    monkeypatch.setenv("MEMORIST_OBJECT_STORE_PATH", str(tmp_path / "objects"))
    return TestClient(create_app()), db_path


def test_model_control_roles(client_and_db: tuple[TestClient, Path]) -> None:
    client, _db_path = client_and_db
    response = client.get("/memcore/model-control/roles")

    assert response.status_code == 200
    roles = {item["role"]: item for item in response.json()["items"]}
    assert {
        "main_chat_observed",
        "preflight",
        "memory_extraction",
        "embedding",
    }.issubset(roles)
    assert roles["main_chat_observed"]["controlled_by_memorist"] is False
    assert roles["preflight"]["fail_open"] is True
    assert roles["memory_extraction"]["blocks_main_request"] is False
    assert roles["embedding"]["lifecycle"].startswith("asynchronous")


def test_role_contract_manifest_uses_each_roles_actual_contract() -> None:
    extraction = role_contract_manifest("memory_extraction")
    reconstruction = role_contract_manifest("import_reconstruction")
    bundle = extraction["bundle"]
    assert bundle["bundle_id"] == "memory-extraction-contract-bundle-v1"
    assert [
        (entry["prompt"]["prompt_id"], entry["prompt"]["prompt_version"])
        for entry in bundle["prompts"]
    ] == [
        ("memorist.jakobson_sentence_analysis", "3.0"),
        ("memorist.semantic_candidate_analysis", "1.0"),
    ]
    assert reconstruction["prompt"]["metadata"]["prompt_id"] == "memorist.import_reconstruction"
    assert reconstruction["prompt"]["metadata"]["prompt_version"] == "2.0"
    assert extraction != reconstruction
    assert role_contract_manifest("main_chat_observed")["certifiable"] is False


def test_processing_nodes_selectable_roles_exclude_openwebui_main_chat() -> None:
    source = (ROOT / "open-webui-integration/memorist/ui/processingNodes.ts").read_text()

    assert "MEMORIST_PROCESSING_NODE_SELECTABLE_ROLES" in source
    assert 'role !== "main_chat_observed"' in source
    assert "MEMORIST_PROCESSING_NODE_SELECTABLE_ROLES.map" in source
    assert "MEMORIST_MODEL_ROLES.map((role) => `<option" not in source
    assert "Selected in Open WebUI; Memorist observes metadata only." in source


def test_model_control_profile_crud(
    client_and_db: tuple[TestClient, Path],
    openai_compatible_server: str,
) -> None:
    client, _db_path = client_and_db
    created = _assert_ok(
        client.post(
            "/memcore/model-control/profiles",
            json={
                "provider_type": "openai_compatible_llm",
                "provider_name": "local-mock",
                "model_name": "mock-chat",
                "role": "preflight",
                "endpoint_url": openai_compatible_server,
                "supports_json_mode": True,
                "cost_profile": {"currency": "USD", "input_per_1k": 0.01},
            },
        )
    )

    assert created["provider_type"] == "openai_compatible_llm"
    assert created["endpoint_is_local"] is True
    assert "secret_env_var_name" not in created

    profile_uuid = created["model_profile_uuid"]
    fetched = _assert_ok(client.get(f"/memcore/model-control/profiles/{profile_uuid}"))
    assert fetched["model_name"] == "mock-chat"

    patched = _assert_ok(
        client.patch(
            f"/memcore/model-control/profiles/{profile_uuid}",
            json={"quality_profile": "fast-local"},
        )
    )
    assert patched["quality_profile"] == "fast-local"

    health = _assert_ok(
        client.post(f"/memcore/model-control/profiles/{profile_uuid}/test", json={})
    )
    assert health["health"]["status"] == "ok"
    with _db(_db_path) as connection:
        event = connection.execute(
            """
            SELECT *
            FROM model_health_events
            WHERE model_profile_uuid = ?
            """,
            (profile_uuid,),
        ).fetchone()
        assert event is not None
        assert event["status"] == "ok"

    rejected = client.post(
        "/memcore/model-control/profiles",
        json={
            "provider_type": "deterministic",
            "model_name": "bad",
            "role": "preflight",
            "metadata": {"api_key": "must-not-store"},
        },
    )
    assert rejected.status_code == 422


def test_model_control_profile_test_records_sanitized_health_event(
    client_and_db: tuple[TestClient, Path],
) -> None:
    client, db_path = client_and_db

    with _served(_AuthFailureHandler) as endpoint_url:
        created = _assert_ok(
            client.post(
                "/memcore/model-control/profiles",
                json={
                    "provider_type": "openai_compatible_llm",
                    "model_name": "mock-chat",
                    "role": "preflight",
                    "endpoint_url": endpoint_url,
                    "supports_json_mode": True,
                },
            )
        )
        profile_uuid = created["model_profile_uuid"]

        response_payload = _assert_ok(
            client.post(f"/memcore/model-control/profiles/{profile_uuid}/test", json={})
        )

    detail = response_payload["health"]["detail"]
    assert response_payload["health"]["status"] == "error"
    assert "HTTP 401" in detail
    _assert_no_auth_failure_secret_material(detail)
    assert "Bearer [redacted]" in detail
    assert "api_key=[redacted]" in detail
    assert "token=[redacted]" in detail
    assert "secret: [redacted]" in detail
    assert "https://example.test/v1?token=%5Bredacted%5D" in detail

    with _db(db_path) as connection:
        event = connection.execute(
            """
            SELECT detail_sanitized
            FROM model_health_events
            WHERE model_profile_uuid = ?
            """,
            (profile_uuid,),
        ).fetchone()
    assert event is not None
    assert event["detail_sanitized"] == detail
    _assert_no_auth_failure_secret_material(event["detail_sanitized"])


def test_openai_compatible_health_check_validates_json_mode(
    openai_json_mode_server: tuple[str, type[Any]],
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


def test_structured_role_health_is_incompatible_without_declared_json_capability(
    openai_json_mode_server: tuple[str, type[Any]],
) -> None:
    endpoint, handler = openai_json_mode_server
    provider = OpenAICompatibleLLMProvider(
        endpoint,
        "mock-chat",
        requires_structured_extraction=True,
    )

    health = provider.health_check()

    assert health.status == "error"
    assert health.overall_status == "incompatible"
    assert health.role_compatibility_status == "incompatible"
    assert health.structured_output_status == "not_declared"
    assert health.failure_stage == "capability_declaration"
    assert "response_format" not in handler.last_payload
    assert health.detail is not None
    assert "authentication and chat completions validated" in health.detail
    assert "requires the profile to declare Supports JSON mode" in health.detail


def test_openai_compatible_health_check_reports_json_mode_unsupported(
    openai_json_mode_server: tuple[str, type[Any]],
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


def test_role_defaults_resolution(client_and_db: tuple[TestClient, Path]) -> None:
    client, _db_path = client_and_db
    workspace = _assert_ok(client.post("/memcore/workspaces", json={"name": "Workspace"}))
    project = _assert_ok(
        client.post(
            "/memcore/projects",
            json={"workspace_uuid": workspace["workspace_uuid"], "name": "Project"},
        )
    )
    global_profile = _create_profile(client, "preflight", "global-preflight")
    workspace_profile = _create_profile(client, "preflight", "workspace-preflight")
    project_profile = _create_profile(client, "preflight", "project-preflight")

    _assert_ok(
        client.post(
            "/memcore/model-control/defaults",
            json={"role": "preflight", "model_profile_uuid": global_profile},
        )
    )
    _assert_ok(
        client.post(
            "/memcore/model-control/defaults",
            json={
                "role": "preflight",
                "model_profile_uuid": workspace_profile,
                "workspace_uuid": workspace["workspace_uuid"],
            },
        )
    )
    _assert_ok(
        client.post(
            "/memcore/model-control/defaults",
            json={
                "role": "preflight",
                "model_profile_uuid": project_profile,
                "workspace_uuid": workspace["workspace_uuid"],
                "project_uuid": project["project_uuid"],
            },
        )
    )

    project_default = _assert_ok(
        client.get(
            "/memcore/model-control/defaults",
            params={
                "role": "preflight",
                "workspace_uuid": workspace["workspace_uuid"],
                "project_uuid": project["project_uuid"],
            },
        )
    )
    workspace_default = _assert_ok(
        client.get(
            "/memcore/model-control/defaults",
            params={"role": "preflight", "workspace_uuid": workspace["workspace_uuid"]},
        )
    )
    global_default = _assert_ok(
        client.get("/memcore/model-control/defaults", params={"role": "preflight"})
    )

    assert project_default["item"]["model_profile_uuid"] == project_profile
    assert workspace_default["item"]["model_profile_uuid"] == workspace_profile
    assert global_default["item"]["model_profile_uuid"] == global_profile


def test_remote_ack_required(client_and_db: tuple[TestClient, Path]) -> None:
    client, _db_path = client_and_db
    created = _assert_ok(
        client.post(
            "/memcore/model-control/profiles",
            json={
                "provider_type": "openai_compatible_llm",
                "provider_name": "external",
                "model_name": "external-memory-model",
                "role": "memory_extraction",
                "endpoint_url": "https://models.example.test",
                "endpoint_is_local": False,
                "secret_strategy": "env_var",
                "secret_env_var_name": "MEMORIST_TEST_PROVIDER_KEY",
            },
        )
    )

    assert "secret_env_var_name" not in created
    denied = client.post(
        "/memcore/model-control/defaults",
        json={
            "role": "memory_extraction",
            "model_profile_uuid": created["model_profile_uuid"],
        },
    )
    assert denied.status_code == 409

    patched = _assert_ok(
        client.patch(
            f"/memcore/model-control/profiles/{created['model_profile_uuid']}",
            json={"privacy_acknowledged": True},
        )
    )
    assert patched["privacy_acknowledged_at"] is not None
    ack = _assert_ok(
        client.post(
            "/memcore/model-control/privacy/acknowledge",
            json={
                "model_profile_uuid": created["model_profile_uuid"],
                "acknowledged_risk_level": "external",
                "acknowledged_data_sent": {"sends_raw_user_text": True},
            },
        )
    )
    assert ack["role"] == "memory_extraction"
    assert ack["acknowledged_data_sent"]["sends_raw_user_text"] is True
    uncertified = client.post(
        "/memcore/model-control/defaults",
        json={
            "role": "memory_extraction",
            "model_profile_uuid": created["model_profile_uuid"],
        },
    )
    assert uncertified.status_code == 400
    assert "certification" in uncertified.json()["detail"]

    rejected = client.post(
        "/memcore/model-control/profiles",
        json={
            "provider_type": "openai_compatible_llm",
            "model_name": "leaky",
            "role": "preflight",
            "endpoint_url": "https://models.example.test/v1?api_key=raw",
        },
    )
    assert rejected.status_code == 422


def test_certification_is_server_authoritative_stale_and_removable(
    client_and_db: tuple[TestClient, Path],
    openai_compatible_server: str,
) -> None:
    client, _db_path = client_and_db
    _OpenAICompatibleHandler.reset()
    created = _assert_ok(
        client.post(
            "/memcore/model-control/profiles",
            json={
                "provider_type": "openai_compatible_llm",
                "provider_name": "controlled-local",
                "model_name": "mock-chat",
                "role": "memory_extraction",
                "endpoint_url": openai_compatible_server,
                "endpoint_is_local": True,
                "supports_json_mode": True,
            },
        )
    )
    profile_uuid = created["model_profile_uuid"]
    assert created["certification_status"] == "missing"
    assert created["certification_current"] is False

    blocked = client.post(
        "/memcore/model-control/defaults",
        json={"role": "memory_extraction", "model_profile_uuid": profile_uuid},
    )
    assert blocked.status_code == 400
    assert "status=missing" in blocked.json()["detail"]

    tested = _assert_ok(
        client.post(f"/memcore/model-control/profiles/{profile_uuid}/test", json={})
    )
    assert tested["certification"]["certification_status"] == "current"
    assert tested["certification"]["certification_current"] is True
    refreshed = _assert_ok(client.get("/memcore/model-control/profiles"))["items"]
    persisted = next(item for item in refreshed if item["model_profile_uuid"] == profile_uuid)
    assert persisted["certification_current"] is True

    _assert_ok(
        client.post(
            "/memcore/model-control/defaults",
            json={"role": "memory_extraction", "model_profile_uuid": profile_uuid},
        )
    )
    changed = _assert_ok(
        client.patch(
            f"/memcore/model-control/profiles/{profile_uuid}",
            json={"model_name": "changed-model"},
        )
    )
    assert changed["certification_status"] == "stale"
    assert changed["certification_current"] is False
    effective = _assert_ok(client.get("/memcore/model-control/defaults?role=memory_extraction"))[
        "item"
    ]
    assert effective["scope_source"] == "built_in_fallback"
    assert effective["fallback_reason"] == "provider_certification_stale"

    stale_blocked = client.post(
        "/memcore/model-control/defaults",
        json={"role": "memory_extraction", "model_profile_uuid": profile_uuid},
    )
    assert stale_blocked.status_code == 400
    assert "status=stale" in stale_blocked.json()["detail"]

    removed = _assert_ok(client.delete("/memcore/model-control/defaults?role=memory_extraction"))
    assert removed["removed"] is True
    assert _assert_ok(client.get("/memcore/model-control/defaults"))["items"] == []


def test_generic_health_marker_cannot_certify_memory_extraction_contract(
    client_and_db: tuple[TestClient, Path],
) -> None:
    client, _db_path = client_and_db
    with _served(_HealthCheckHandler) as endpoint_url:
        created = _assert_ok(
            client.post(
                "/memcore/model-control/profiles",
                json={
                    "provider_type": "openai_compatible_llm",
                    "model_name": "mock-chat",
                    "role": "memory_extraction",
                    "endpoint_url": endpoint_url,
                    "endpoint_is_local": True,
                    "supports_json_mode": True,
                },
            )
        )
        result = _assert_ok(
            client.post(
                f"/memcore/model-control/profiles/{created['model_profile_uuid']}/test",
                json={},
            )
        )
    assert result["health"]["overall_status"] == "incompatible"
    assert result["health"]["failure_stage"] == "role_contract_probe"
    assert result["health"]["role_probe_status"] == "incompatible"
    assert result["certification"]["certification_status"] == "failed"
    rejected = client.post(
        "/memcore/model-control/defaults",
        json={
            "role": "memory_extraction",
            "model_profile_uuid": created["model_profile_uuid"],
        },
    )
    assert rejected.status_code == 400


@pytest.mark.parametrize(
    "role",
    [
        "preflight",
        "import_reconstruction",
        "high_confidence_extraction",
        "block_compaction",
        "privacy_sensitivity",
    ],
)
def test_generic_health_marker_cannot_certify_other_structured_roles(role: str) -> None:
    from memcore.model_control.registry import test_profile_health

    with _served(_HealthCheckHandler) as endpoint_url:
        health = test_profile_health(
            {
                "provider_type": "openai_compatible_llm",
                "model_name": "mock-chat",
                "role": role,
                "endpoint_url": endpoint_url,
                "supports_json_mode": True,
            }
        )
    assert health.overall_status == "incompatible"
    assert health.failure_stage == "role_contract_probe"
    assert health.role_probe_status == "incompatible"


def test_preflight_provider_fail_open(client_and_db: tuple[TestClient, Path]) -> None:
    client, db_path = client_and_db
    session = _assert_ok(client.post("/memcore/sessions", json={"title": "preflight"}))
    message = _assert_ok(
        client.post(
            "/memcore/messages",
            json={
                "session_uuid": session["session_uuid"],
                "role": "user",
                "creator_type": "user",
                "raw_text": "Please remember this.",
            },
        )
    )
    workspace_uuid = _authorize_turn(db_path, session["session_uuid"], message["message_uuid"])

    response = _assert_ok(
        client.post(
            "/memcore/preflight",
            json={
                "session_uuid": session["session_uuid"],
                "input_message_uuid": message["message_uuid"],
                "retrieval_mode": "invalid-mode",
                "user_uuid": "test-user",
                "workspace_uuid": workspace_uuid,
            },
        )
    )
    assert response["status"] == "failed_open"

    usage = _assert_ok(client.get("/memcore/model-control/usage"))
    assert any(
        item["role"] == "preflight" and item["stage"] == "preflight" for item in usage["items"]
    )


def test_preflight_model_lifecycle_records_before_attachment(
    client_and_db: tuple[TestClient, Path],
    openai_compatible_server: str,
) -> None:
    client, db_path = client_and_db
    _OpenAICompatibleHandler.reset()
    _OpenAICompatibleHandler.expected_chat_model = "mock-preflight"
    profile = _assert_ok(
        client.post(
            "/memcore/model-control/profiles",
            json={
                "provider_type": "openai_compatible",
                "provider_name": "local-invalid-preflight",
                "model_name": "mock-preflight",
                "role": "preflight",
                "endpoint_url": openai_compatible_server,
                "supports_json_mode": True,
            },
        )
    )
    _assert_ok(
        client.post(
            f"/memcore/model-control/profiles/{profile['model_profile_uuid']}/test",
            json={},
        )
    )
    _assert_ok(
        client.post(
            "/memcore/model-control/defaults",
            json={"role": "preflight", "model_profile_uuid": profile["model_profile_uuid"]},
        )
    )
    _OpenAICompatibleHandler.response_content = "not-json"
    session = _assert_ok(client.post("/memcore/sessions", json={"title": "preflight-model"}))
    message = _assert_ok(
        client.post(
            "/memcore/messages",
            json={
                "session_uuid": session["session_uuid"],
                "role": "user",
                "creator_type": "user",
                "raw_text": "Remember that this project uses a role matrix.",
            },
        )
    )
    workspace_uuid = _authorize_turn(db_path, session["session_uuid"], message["message_uuid"])

    response = _assert_ok(
        client.post(
            "/memcore/preflight",
            json={
                "session_uuid": session["session_uuid"],
                "input_message_uuid": message["message_uuid"],
                "recent_conversation_text": "Remember that this project uses a role matrix.",
                "user_uuid": "test-user",
                "workspace_uuid": workspace_uuid,
            },
        )
    )
    assert response["status"] in {"abstained", "attached", "timeout", "failed_open"}

    with _db(db_path) as connection:
        events = [
            row["event_type"]
            for row in connection.execute(
                """
                SELECT event_type
                FROM preflight_events
                WHERE input_message_uuid = ?
                ORDER BY rowid
                """,
                (message["message_uuid"],),
            )
        ]
        assert "retrieval_completed" in events
        assert "preflight_model_started" in events
        assert "preflight_model_failed_open" in events
        assert events.index("retrieval_completed") < events.index("preflight_model_started")
        usage = connection.execute(
            """
            SELECT *
            FROM model_usage_events
            WHERE role = 'preflight' AND stage = 'preflight_model'
            ORDER BY created_at DESC
            LIMIT 1
            """
        ).fetchone()
        assert usage is not None
        assert usage["model_name"] == "mock-preflight"
        assert usage["status"] == "failed_open"


def test_extraction_uses_memory_model_not_chat_model(
    client_and_db: tuple[TestClient, Path],
    openai_compatible_server: str,
) -> None:
    client, db_path = client_and_db
    session = _assert_ok(
        client.post(
            "/memcore/openwebui/session/resolve",
            json={
                "openwebui_conversation_id": "chat-model-control",
                "title": "Chat",
                "user_id": "model-control-user",
            },
        )
    )
    chat_profile = _create_profile(client, "main_chat_observed", "openwebui-chat-model")
    extraction_profile = _create_certified_extraction_profile(
        client, openai_compatible_server, "memory-extractor"
    )
    _assert_ok(
        client.post(
            "/memcore/model-control/defaults",
            json={"role": "main_chat_observed", "model_profile_uuid": chat_profile},
        )
    )
    _assert_ok(
        client.post(
            "/memcore/model-control/defaults",
            json={"role": "memory_extraction", "model_profile_uuid": extraction_profile},
        )
    )

    capture = _assert_ok(
        client.post(
            "/memcore/openwebui/messages/capture",
            json={
                "session_uuid": session["session_uuid"],
                "openwebui_message_id": "assistant-1",
                "role": "assistant",
                "content": "The answer is ready.",
                "idempotency_key": "assistant-capture-1",
            },
        )
    )
    assert capture["duplicate"] is False

    with _db(db_path) as connection:
        job = connection.execute(
            """
            SELECT *
            FROM jobs
            WHERE job_type = 'memory_extraction'
            ORDER BY created_at DESC
            LIMIT 1
            """
        ).fetchone()
        assert job is not None
        payload = load_ijson(job["payload_ijson"])
        assert payload["model_role"] == "memory_extraction"
        assert payload["message_uuid"] == capture["message_uuid"]

        usage = connection.execute(
            """
            SELECT *
            FROM model_usage_events
            WHERE role = 'memory_extraction'
            ORDER BY created_at DESC
            LIMIT 1
            """
        ).fetchone()
        assert usage is not None
        assert usage["stage"] == "memory_extraction_queued"
        assert usage["model_name"] == "memory-extractor"


def test_memory_worker_process_uses_memory_extraction_profile(
    client_and_db: tuple[TestClient, Path],
    openai_compatible_server: str,
    tmp_path: Path,
) -> None:
    client, db_path = client_and_db
    extraction_profile = _create_certified_extraction_profile(
        client, openai_compatible_server, "worker-extractor"
    )
    _assert_ok(
        client.post(
            "/memcore/model-control/defaults",
            json={"role": "memory_extraction", "model_profile_uuid": extraction_profile},
        )
    )
    session = _assert_ok(client.post("/memcore/sessions", json={"title": "worker"}))
    message = _assert_ok(
        client.post(
            "/memcore/messages",
            json={
                "session_uuid": session["session_uuid"],
                "role": "assistant",
                "creator_type": "model",
                "raw_text": "The user prefers deterministic local model-control tests.",
            },
        )
    )

    with _db(db_path) as connection:
        result = MemoryWorkerPipeline(
            connection,
            Settings(db_path=str(db_path), object_store_path=str(tmp_path / "objects-worker")),
        ).process_message(message["message_uuid"])
        assert result["model_profile_uuid"] == extraction_profile
        usage = connection.execute(
            """
            SELECT *
            FROM model_usage_events
            WHERE role = 'memory_extraction' AND stage = 'jakobson_sentence_analysis'
            ORDER BY created_at DESC
            LIMIT 1
            """
        ).fetchone()
        assert usage is not None
        assert usage["model_name"] == "worker-extractor"


def test_memory_worker_uses_tested_memory_extraction_profile(
    client_and_db: tuple[TestClient, Path],
    openai_compatible_server: str,
    tmp_path: Path,
) -> None:
    client, db_path = client_and_db
    _OpenAICompatibleHandler.reset()
    profile = _assert_ok(
        client.post(
            "/memcore/model-control/profiles",
            json={
                "provider_type": "openai_compatible_llm",
                "provider_name": "local-memory-mock",
                "model_name": "mock-chat",
                "role": "memory_extraction",
                "endpoint_url": openai_compatible_server,
                "supports_json_mode": True,
            },
        )
    )
    profile_uuid = profile["model_profile_uuid"]

    health = _assert_ok(
        client.post(f"/memcore/model-control/profiles/{profile_uuid}/test", json={})
    )

    assert health["health"]["status"] == "ok"
    assert _OpenAICompatibleHandler.post_paths == [
        "/v1/chat/completions",
        "/v1/chat/completions",
        "/v1/chat/completions",
    ]
    assert _OpenAICompatibleHandler.last_payload["model"] == "mock-chat"
    raw_text = "The user prefers local OpenAI-compatible memory extraction tests."
    provider_output = canonical_jakobson_v3_example()
    provider_output["items"][0]["text"] = raw_text
    semantic_output = canonical_semantic_candidate_v1_example()
    semantic_output["semantic_units"][0].update(
        {
            "raw_end": len(raw_text),
            "evidence": raw_text,
            "proposition": raw_text,
            "unit_type": "statement",
        }
    )
    _OpenAICompatibleHandler.response_content_by_prompt_id = {
        "memorist.jakobson_sentence_analysis": json.dumps(provider_output),
        "memorist.semantic_candidate_analysis": json.dumps(semantic_output),
    }

    _assert_ok(
        client.post(
            "/memcore/model-control/defaults",
            json={"role": "memory_extraction", "model_profile_uuid": profile_uuid},
        )
    )
    session = _assert_ok(client.post("/memcore/sessions", json={"title": "worker-openai"}))
    message = _assert_ok(
        client.post(
            "/memcore/messages",
            json={
                "session_uuid": session["session_uuid"],
                "role": "assistant",
                "creator_type": "model",
                "raw_text": raw_text,
            },
        )
    )

    with _db(db_path) as connection:
        result = MemoryWorkerPipeline(
            connection,
            Settings(db_path=str(db_path), object_store_path=str(tmp_path / "objects-openai")),
        ).process_message(message["message_uuid"])
        assert result["model_profile_uuid"] == profile_uuid

        usage = connection.execute(
            """
            SELECT model_profile_uuid, provider_type, model_name, status
            FROM model_usage_events
            WHERE role = 'memory_extraction' AND stage = 'jakobson_sentence_analysis'
            ORDER BY created_at DESC
            LIMIT 1
            """
        ).fetchone()
        assert usage is not None
        assert usage["model_profile_uuid"] == profile_uuid
        assert usage["provider_type"] == "openai_compatible_llm"
        assert usage["model_name"] == "mock-chat"
        assert usage["status"] == "ok"

        prompt_runs = connection.execute(
            """
            SELECT prompt_id, model_profile_uuid, provider_type, model_name, status
            FROM prompt_execution_runs
            WHERE model_role = 'memory_extraction' AND message_uuid = ?
            ORDER BY prompt_id
            """,
            (message["message_uuid"],),
        ).fetchall()
        runs_by_prompt = {row["prompt_id"]: row for row in prompt_runs}
        assert set(runs_by_prompt) == {
            "memorist.jakobson_sentence_analysis",
            "memorist.semantic_candidate_analysis",
        }
        for prompt_id, expected_status in {
            "memorist.jakobson_sentence_analysis": "ok",
            "memorist.semantic_candidate_analysis": "ok",
        }.items():
            prompt_run = runs_by_prompt[prompt_id]
            assert prompt_run["model_profile_uuid"] == profile_uuid
            assert prompt_run["provider_type"] == "openai_compatible_llm"
            assert prompt_run["model_name"] == "mock-chat"
            assert prompt_run["status"] == expected_status


def test_deterministic_fallback_still_works_without_profile(
    client_and_db: tuple[TestClient, Path],
    tmp_path: Path,
) -> None:
    client, db_path = client_and_db
    session = _assert_ok(client.post("/memcore/sessions", json={"title": "worker-fallback"}))
    message = _assert_ok(
        client.post(
            "/memcore/messages",
            json={
                "session_uuid": session["session_uuid"],
                "role": "user",
                "creator_type": "user",
                "raw_text": "Please remember that deterministic fallback stays available.",
            },
        )
    )

    with _db(db_path) as connection:
        result = MemoryWorkerPipeline(
            connection,
            Settings(db_path=str(db_path), object_store_path=str(tmp_path / "objects-fallback")),
        ).process_message(message["message_uuid"])
        assert result["model_profile_uuid"] is None

        usage = connection.execute(
            """
            SELECT model_profile_uuid, provider_type, model_name, status
            FROM model_usage_events
            WHERE role = 'memory_extraction' AND stage = 'jakobson_sentence_analysis'
            ORDER BY created_at DESC
            LIMIT 1
            """
        ).fetchone()
        assert usage is not None
        assert usage["model_profile_uuid"] is None
        assert usage["provider_type"] == "deterministic"
        assert usage["model_name"] == "deterministic_extraction"
        assert usage["status"] == "ok"

        prompt_run = connection.execute(
            """
            SELECT model_profile_uuid, provider_type, model_name, status
            FROM prompt_execution_runs
            WHERE model_role = 'memory_extraction'
              AND prompt_id = 'memorist.jakobson_sentence_analysis'
              AND message_uuid = ?
            """,
            (message["message_uuid"],),
        ).fetchone()
        assert prompt_run is not None
        assert prompt_run["model_profile_uuid"] is None
        assert prompt_run["provider_type"] == "deterministic"
        assert prompt_run["model_name"] == "deterministic_extraction"
        assert prompt_run["status"] == "ok"


def test_memory_worker_profile_test_failure_is_sanitized_and_fallback_still_works(
    client_and_db: tuple[TestClient, Path],
    tmp_path: Path,
) -> None:
    client, db_path = client_and_db
    with _served(_AuthFailureHandler) as endpoint_url:
        profile = _assert_ok(
            client.post(
                "/memcore/model-control/profiles",
                json={
                    "provider_type": "openai_compatible_llm",
                    "model_name": "mock-chat",
                    "role": "memory_extraction",
                    "endpoint_url": endpoint_url,
                    "supports_json_mode": True,
                },
            )
        )
        profile_uuid = profile["model_profile_uuid"]
        response = _assert_ok(
            client.post(f"/memcore/model-control/profiles/{profile_uuid}/test", json={})
        )

    detail = response["health"]["detail"]
    assert response["health"]["status"] == "error"
    assert "HTTP 401" in detail
    _assert_no_auth_failure_secret_material(detail)
    assert "Bearer [redacted]" in detail
    assert "api_key=[redacted]" in detail

    session = _assert_ok(client.post("/memcore/sessions", json={"title": "worker-failure"}))
    message = _assert_ok(
        client.post(
            "/memcore/messages",
            json={
                "session_uuid": session["session_uuid"],
                "role": "assistant",
                "creator_type": "model",
                "raw_text": (
                    "The memory worker should keep deterministic fallback when no default exists."
                ),
            },
        )
    )

    with _db(db_path) as connection:
        event = connection.execute(
            """
            SELECT status, provider_type, model_name, detail_sanitized
            FROM model_health_events
            WHERE model_profile_uuid = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (profile_uuid,),
        ).fetchone()
        assert event is not None
        assert event["status"] == "error"
        assert event["provider_type"] == "openai_compatible_llm"
        assert event["model_name"] == "mock-chat"
        assert event["detail_sanitized"] == detail
        _assert_no_auth_failure_secret_material(event["detail_sanitized"])

        result = MemoryWorkerPipeline(
            connection,
            Settings(db_path=str(db_path), object_store_path=str(tmp_path / "objects-failure")),
        ).process_message(message["message_uuid"])
        assert result["model_profile_uuid"] is None

        usage = connection.execute(
            """
            SELECT model_profile_uuid, provider_type, model_name, status
            FROM model_usage_events
            WHERE role = 'memory_extraction' AND stage = 'jakobson_sentence_analysis'
            ORDER BY created_at DESC
            LIMIT 1
            """
        ).fetchone()
        assert usage is not None
        assert usage["model_profile_uuid"] is None
        assert usage["provider_type"] == "deterministic"
        assert usage["model_name"] == "deterministic_extraction"
        assert usage["status"] == "ok"


def test_embedding_profile_switch_marks_reindex(client_and_db: tuple[TestClient, Path]) -> None:
    client, db_path = client_and_db
    first_profile = _create_profile(client, "embedding", "embedder-a", supports_embeddings=True)
    second_profile = _create_profile(client, "embedding", "embedder-b", supports_embeddings=True)
    _assert_ok(
        client.post(
            "/memcore/model-control/defaults",
            json={"role": "embedding", "model_profile_uuid": first_profile},
        )
    )
    with _db(db_path) as connection:
        ModelControlRepository(connection).record_embedding(
            first_profile,
            source_type="memory_version",
            source_uuid="00000000-0000-0000-0000-000000000001",
            content_hash="hash-a",
            vector_store_ref="sqlite:memory-version:1",
        )

    _assert_ok(
        client.post(
            "/memcore/model-control/defaults",
            json={"role": "embedding", "model_profile_uuid": second_profile},
        )
    )

    with _db(db_path) as connection:
        row = connection.execute("SELECT stale_at FROM embedding_records").fetchone()
        assert row is not None
        assert row["stale_at"] is not None
        usage = connection.execute(
            """
            SELECT *
            FROM model_usage_events
            WHERE role = 'embedding' AND stage = 'embedding_recorded'
            """
        ).fetchone()
        assert usage is not None
        assert usage["model_name"] == "embedder-a"


def test_cost_estimation_no_secret(client_and_db: tuple[TestClient, Path]) -> None:
    client, _db_path = client_and_db
    profile_uuid = _create_profile(
        client,
        "preflight",
        "priced-preflight",
        cost_profile={"currency": "USD", "input_per_1k": 0.10, "output_per_1k": 0.20},
    )
    estimate = _assert_ok(
        client.post(
            "/memcore/model-control/estimate-cost",
            json={
                "model_profile_uuid": profile_uuid,
                "input_tokens": 1000,
                "output_tokens": 500,
            },
        )
    )
    assert estimate["estimated_cost"] == 0.2
    assert json.dumps(estimate).find("secret") == -1

    costs = _assert_ok(client.get("/memcore/costs/model-roles"))
    assert costs["status"] == "ok"
    assert "local compute" in costs["local_compute_note"].lower()

    privacy = _assert_ok(client.get("/memcore/model-control/privacy"))
    assert any(
        item["role"] == "preflight"
        and "cost_profile" in item
        and "latency_profile" in item
        and "quality_profile" in item
        and "privacy_profile" in item
        for item in privacy["items"]
    )


def test_openapi_contains_model_control(client_and_db: tuple[TestClient, Path]) -> None:
    client, _db_path = client_and_db
    openapi = _assert_ok(client.get("/openapi.json"))

    assert "/memcore/model-control/roles" in openapi["paths"]
    assert "/memcore/model-control/profiles" in openapi["paths"]
    assert "/memcore/model-control/defaults" in openapi["paths"]
    assert "/memcore/model-control/privacy/acknowledge" in openapi["paths"]
    assert "/memcore/costs/model-roles" in openapi["paths"]


def test_ui_model_settings_contract() -> None:
    model_control = (ROOT / "open-webui-integration/memorist/ui/modelControl.ts").read_text(
        encoding="utf-8"
    )
    surfaces = (ROOT / "open-webui-integration/memorist/ui/surfaces.ts").read_text(encoding="utf-8")
    client = (ROOT / "open-webui-integration/memorist/ui/memoristClient.ts").read_text(
        encoding="utf-8"
    )
    processing_nodes = (ROOT / "open-webui-integration/memorist/ui/processingNodes.ts").read_text(
        encoding="utf-8"
    )

    assert "main_chat_observed" in model_control
    assert "memory_extraction" in model_control
    assert "FIRST_RUN_MODEL_DEFAULTS" in model_control
    assert "ROLE_MATRIX_COLUMNS" in model_control
    # PR5-G: the surface inventory only advertises mounted production
    # surfaces; model-role configuration ships as the Processing Nodes page.
    assert "MemoristProcessingNodesSettings" in surfaces
    assert "ModelControlTab" not in surfaces
    assert '"Processing Nodes"' in surfaces
    assert "modelControlProfiles" in client
    assert "/model-control/privacy" in client
    assert "/costs/model-roles" in client
    assert "/settings/memorist/processing-nodes" in processing_nodes
    assert "customElements.define" in processing_nodes
    assert "acknowledgeModelControlPrivacy" in processing_nodes


def test_docs_include_model_roles() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    architecture = (ROOT / "docs/ARCHITECTURE.md").read_text(encoding="utf-8")

    assert "Model Control Plane" in readme
    assert "main_chat_observed" in readme
    assert "memory_extraction" in readme
    assert "privacy acknowledgement" in readme
    assert "Model Control Plane path" in architecture
    assert "embedding" in architecture


def test_openai_compatible_health_check_uses_chat_completions_not_models(
    openai_compatible_server: str,
) -> None:
    _OpenAICompatibleHandler.reset()

    health = OpenAICompatibleLLMProvider(
        openai_compatible_server,
        "mock-chat",
        supports_json_mode=True,
    ).health_check()

    assert health.status == "ok"
    assert _OpenAICompatibleHandler.get_paths == []
    assert _OpenAICompatibleHandler.post_paths == ["/v1/chat/completions"]
    assert _OpenAICompatibleHandler.request_log == [
        {
            "method": "POST",
            "path": "/v1/chat/completions",
            "json": _OpenAICompatibleHandler.last_payload,
            "body": _OpenAICompatibleHandler.last_body,
        }
    ]
    assert _OpenAICompatibleHandler.last_payload["model"] == "mock-chat"


def test_openai_compatible_embedding_health_check_uses_embeddings(
    openai_compatible_server: str,
) -> None:
    _OpenAICompatibleHandler.reset()

    health = OpenAICompatibleEmbeddingProvider(
        openai_compatible_server,
        "mock-embedding",
        embedding_dimension=3,
    ).health_check()

    assert health.status == "ok"
    assert health.local_only_safe is True
    assert health.detail is not None
    assert "dimension=3" in health.detail
    assert _OpenAICompatibleHandler.get_paths == []
    assert _OpenAICompatibleHandler.post_paths == ["/v1/embeddings"]
    assert _OpenAICompatibleHandler.last_payload == {
        "model": "mock-embedding",
        "input": ["Memorist embedding connectivity test."],
    }


def test_openai_compatible_embedding_health_check_dimension_mismatch(
    openai_compatible_server: str,
) -> None:
    _OpenAICompatibleHandler.reset()

    health = OpenAICompatibleEmbeddingProvider(
        openai_compatible_server,
        "mock-embedding",
        embedding_dimension=4,
    ).health_check()

    assert health.status == "error"
    assert health.detail is not None
    assert "Embedding dimension mismatch" in health.detail
    assert "profile expects 4" in health.detail
    assert "provider returned 3" in health.detail
    assert "Update the profile embedding_dimension" in health.detail
    assert _OpenAICompatibleHandler.post_paths == ["/v1/embeddings"]


def test_openai_compatible_health_check_rejects_malformed_json() -> None:
    class MalformedJSONHandler(_HealthCheckHandler):
        response_content = "not-json"

    with _served(MalformedJSONHandler) as endpoint_url:
        health = OpenAICompatibleLLMProvider(
            endpoint_url,
            "mock-chat",
            supports_json_mode=True,
        ).health_check()

    assert health.status == "error"
    assert health.detail is not None
    assert "Malformed JSON" in health.detail


def test_openai_compatible_health_check_accepts_fenced_json() -> None:
    class FencedJSONHandler(_HealthCheckHandler):
        response_content = '```json\n{"memorist_provider_test":"ok"}\n```'

    with _served(FencedJSONHandler) as endpoint_url:
        health = OpenAICompatibleLLMProvider(
            endpoint_url,
            "mock-chat",
            supports_json_mode=True,
        ).health_check()

    assert health.overall_status == "ok"
    assert health.structured_output_status == "supported"
    assert health.role_compatibility_status == "compatible"


@pytest.mark.parametrize(
    (
        "status_code",
        "overall_status",
        "authentication_status",
        "model_status",
        "retryable",
        "rate_limited",
    ),
    [
        (403, "authentication_failed", "invalid", "unknown", False, False),
        (404, "incompatible", "valid", "not_found", False, False),
        (429, "rate_limited", "valid", "unknown", True, True),
        (500, "unknown_error", "valid", "unknown", True, False),
    ],
)
def test_openai_compatible_health_distinguishes_reachable_http_failures(
    status_code: int,
    overall_status: str,
    authentication_status: str,
    model_status: str,
    retryable: bool,
    rate_limited: bool,
) -> None:
    _ProviderStatusHandler.status_code = status_code
    with _served(_ProviderStatusHandler) as endpoint_url:
        health = OpenAICompatibleLLMProvider(endpoint_url, "mock-chat").health_check()

    assert health.status == "error"
    assert health.http_status == status_code
    assert health.tcp_or_http_reachable == "reachable"
    assert health.overall_status == overall_status
    assert health.authentication_status == authentication_status
    assert health.model_status == model_status
    assert health.retryable is retryable
    assert health.quota_or_rate_limited is rate_limited


def test_openai_compatible_health_reports_missing_secret_without_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MEMORIST_TEST_MISSING_PROVIDER_KEY", raising=False)
    with _served(_HealthCheckHandler) as endpoint_url:
        health = OpenAICompatibleLLMProvider(
            endpoint_url,
            "mock-chat",
            secret_env_var_name="MEMORIST_TEST_MISSING_PROVIDER_KEY",
        ).health_check()

    assert health.overall_status == "misconfigured"
    assert health.authentication_status == "missing_secret_reference"
    assert health.http_status is None


def test_openai_compatible_health_distinguishes_dropped_connection() -> None:
    with _served(_DroppedConnectionHandler) as endpoint_url:
        health = OpenAICompatibleLLMProvider(endpoint_url, "mock-chat").health_check()

    assert health.status == "error"
    assert health.overall_status == "unreachable"
    assert health.tcp_or_http_reachable == "unreachable"
    assert health.retryable is True


def test_openai_compatible_health_check_reports_wrong_model() -> None:
    with _served(_HealthCheckHandler) as endpoint_url:
        health = OpenAICompatibleLLMProvider(endpoint_url, "wrong-chat").health_check()

    assert health.status == "error"
    assert health.model_name == "wrong-chat"
    assert health.detail is not None
    assert "HTTP 400" in health.detail


def test_fake_provider_tracks_each_request_path_and_body(
    openai_compatible_server: str,
) -> None:
    _OpenAICompatibleHandler.reset()

    health = OpenAICompatibleLLMProvider(openai_compatible_server, "mock-chat").health_check()

    assert health.status == "ok"
    assert _OpenAICompatibleHandler.request_log == [
        {
            "method": "POST",
            "path": "/v1/chat/completions",
            "json": _OpenAICompatibleHandler.last_payload,
            "body": _OpenAICompatibleHandler.last_body,
        }
    ]
    assert _OpenAICompatibleHandler.get_paths == []
    assert _OpenAICompatibleHandler.last_payload["model"] == "mock-chat"
    assert b'"model": "mock-chat"' in _OpenAICompatibleHandler.last_body


def test_fake_provider_supports_chat_completion_success(
    openai_compatible_server: str,
) -> None:
    _OpenAICompatibleHandler.reset()

    health = OpenAICompatibleLLMProvider(openai_compatible_server, "mock-chat").health_check()

    assert health.status == "ok"
    assert health.detail == "HTTP 200; chat completions validated"


def test_fake_provider_supports_malformed_json_content(
    openai_compatible_server: str,
) -> None:
    _OpenAICompatibleHandler.reset()
    _OpenAICompatibleHandler.response_content = "not-json"

    health = OpenAICompatibleLLMProvider(openai_compatible_server, "mock-chat").health_check()

    assert health.status == "error"
    assert health.detail is not None
    assert "Malformed JSON" in health.detail


def test_fake_provider_supports_json_mode_rejection(
    openai_compatible_server: str,
) -> None:
    _OpenAICompatibleHandler.reset()
    _OpenAICompatibleHandler.reject_response_format = True

    health = OpenAICompatibleLLMProvider(
        openai_compatible_server,
        "mock-chat",
        supports_json_mode=True,
    ).health_check()

    assert health.status == "error"
    assert health.detail == (
        "Provider rejected JSON response_format; disable Supports JSON mode or "
        "choose a compatible model."
    )


def test_fake_provider_supports_http_401_with_fake_bearer_token_body(
    openai_compatible_server: str,
) -> None:
    _OpenAICompatibleHandler.reset()
    _OpenAICompatibleHandler.auth_failure_body = "Authorization failed for Bearer abc.def.ghi"

    health = OpenAICompatibleLLMProvider(openai_compatible_server, "mock-chat").health_check()

    assert health.status == "error"
    assert health.detail is not None
    assert "HTTP 401" in health.detail
    assert "Bearer [redacted]" in health.detail
    assert "abc.def.ghi" not in health.detail


def test_fake_provider_supports_wrong_model_rejection(
    openai_compatible_server: str,
) -> None:
    _OpenAICompatibleHandler.reset()

    health = OpenAICompatibleLLMProvider(openai_compatible_server, "wrong-chat").health_check()

    assert health.status == "error"
    assert health.detail is not None
    assert "HTTP 400" in health.detail
    assert _OpenAICompatibleHandler.last_payload["model"] == "wrong-chat"


def test_fake_provider_supports_timeout(
    openai_compatible_server: str,
) -> None:
    _OpenAICompatibleHandler.reset()
    _OpenAICompatibleHandler.response_delay_seconds = 0.2

    health = OpenAICompatibleLLMProvider(openai_compatible_server, "mock-chat").health_check(
        timeout_seconds=0.01
    )

    assert health.status == "error"
    assert health.detail is not None
    assert "timed out" in health.detail.lower()


def test_fake_provider_supports_embedding_success(
    openai_compatible_server: str,
) -> None:
    _OpenAICompatibleHandler.reset()

    health = OpenAICompatibleEmbeddingProvider(
        openai_compatible_server,
        "mock-embedding",
        embedding_dimension=3,
    ).health_check()

    assert health.status == "ok"
    assert health.detail is not None
    assert "dimension=3" in health.detail
    assert _OpenAICompatibleHandler.last_payload == {
        "model": "mock-embedding",
        "input": ["Memorist embedding connectivity test."],
    }


def test_fake_provider_supports_embedding_dimension_mismatch(
    openai_compatible_server: str,
) -> None:
    _OpenAICompatibleHandler.reset()
    _OpenAICompatibleHandler.embedding_vector = [0.1, 0.2]

    health = OpenAICompatibleEmbeddingProvider(
        openai_compatible_server,
        "mock-embedding",
        embedding_dimension=3,
    ).health_check()

    assert health.status == "error"
    assert health.detail is not None
    assert "Embedding dimension mismatch" in health.detail
    assert "profile expects 3" in health.detail
    assert "provider returned 2" in health.detail


@pytest.fixture
def openai_json_mode_server() -> Iterator[tuple[str, type[Any]]]:
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
            auth_failure_body = type(self).auth_failure_body
            if auth_failure_body is not None:
                body = auth_failure_body.encode("utf-8")
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


@pytest.fixture
def openai_compatible_server() -> Iterator[str]:
    _OpenAICompatibleHandler.reset()
    server = HTTPServer(("127.0.0.1", 0), _OpenAICompatibleHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        thread.join(timeout=2)


class _OpenAICompatibleHandler(BaseHTTPRequestHandler):
    get_paths: list[str] = []
    post_paths: list[str] = []
    last_payload: dict[str, Any] = {}
    last_body = b""
    request_log: list[dict[str, Any]] = []
    expected_chat_model = "mock-chat"
    expected_embedding_model = "mock-embedding"
    response_content = json.dumps({"memorist_provider_test": "ok"})
    reject_response_format = False
    auth_failure_body: str | None = None
    response_delay_seconds = 0.0
    embedding_vector: list[float] = [0.1, 0.2, 0.3]
    response_content_by_prompt_id: dict[str, str] = {}

    @classmethod
    def reset(cls) -> None:
        cls.get_paths = []
        cls.post_paths = []
        cls.last_payload = {}
        cls.last_body = b""
        cls.request_log = []
        cls.expected_chat_model = "mock-chat"
        cls.expected_embedding_model = "mock-embedding"
        cls.response_content = json.dumps({"memorist_provider_test": "ok"})
        cls.reject_response_format = False
        cls.auth_failure_body = None
        cls.response_delay_seconds = 0.0
        cls.embedding_vector = [0.1, 0.2, 0.3]
        cls.response_content_by_prompt_id = {}

    def do_GET(self) -> None:  # noqa: N802
        self.__class__.get_paths.append(self.path)
        self.__class__.request_log.append({"method": "GET", "path": self.path, "body": b""})
        if self.path == "/v1/models":
            body = json.dumps({"data": [{"id": "mock-chat"}]}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self) -> None:  # noqa: N802
        self.__class__.post_paths.append(self.path)
        if self.__class__.response_delay_seconds:
            time.sleep(self.__class__.response_delay_seconds)
        if self.path == "/v1/chat/completions":
            length = int(self.headers.get("Content-Length", "0"))
            raw_body = self.rfile.read(length)
            payload = json.loads(raw_body.decode("utf-8"))
            self.__class__.last_body = raw_body
            self.__class__.last_payload = payload
            self.__class__.request_log.append(
                {"method": "POST", "path": self.path, "json": payload, "body": raw_body}
            )
            if self.__class__.auth_failure_body is not None:
                body = self.__class__.auth_failure_body.encode("utf-8")
                self.send_response(401)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if self.__class__.reject_response_format and "response_format" in payload:
                body = json.dumps({"error": "response_format is unsupported"}).encode("utf-8")
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if payload.get("model") != self.__class__.expected_chat_model:
                body = json.dumps({"error": {"message": "model not found"}}).encode("utf-8")
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            response_content = self.__class__.response_content
            schema = payload.get("response_format", {}).get("json_schema", {})
            messages = payload.get("messages", [])
            prompt_text = json.dumps(messages, ensure_ascii=False)
            role_output = _role_contract_probe_output(prompt_text)
            prompt_id = None
            if (
                schema.get("name") == "memorist_jakobson_sentence_analysis_v3"
                or "memorist.jakobson_sentence_analysis" in prompt_text
            ):
                prompt_id = "memorist.jakobson_sentence_analysis"
            elif (
                schema.get("name") == "memorist_semantic_candidate_analysis_v1"
                or "memorist.semantic_candidate_analysis" in prompt_text
            ):
                prompt_id = "memorist.semantic_candidate_analysis"
            contract_response = self.__class__.response_content_by_prompt_id.get(prompt_id or "")
            if contract_response is not None:
                response_content = contract_response
            elif role_output is not None and response_content == json.dumps(
                {"memorist_provider_test": "ok"}
            ):
                response_content = json.dumps(role_output)
            elif (
                schema.get("name") == "memorist_jakobson_sentence_analysis_v3"
                or "memorist.jakobson_sentence_analysis" in prompt_text
            ) and response_content == json.dumps({"memorist_provider_test": "ok"}):
                output = canonical_jakobson_v3_example()
                output["items"][0]["text"] = "Keep backups enabled."
                response_content = json.dumps(output)
            elif (
                schema.get("name") == "memorist_semantic_candidate_analysis_v1"
                or "memorist.semantic_candidate_analysis" in prompt_text
            ) and response_content == json.dumps({"memorist_provider_test": "ok"}):
                response_content = json.dumps(canonical_semantic_candidate_v1_example())
            body = json.dumps({"choices": [{"message": {"content": response_content}}]}).encode(
                "utf-8"
            )
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/v1/embeddings":
            length = int(self.headers.get("Content-Length", "0"))
            raw_body = self.rfile.read(length)
            payload = json.loads(raw_body.decode("utf-8"))
            self.__class__.last_body = raw_body
            self.__class__.last_payload = payload
            self.__class__.request_log.append(
                {"method": "POST", "path": self.path, "json": payload, "body": raw_body}
            )
            if payload.get("model") != self.__class__.expected_embedding_model:
                body = json.dumps({"error": {"message": "model not found"}}).encode("utf-8")
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            body = json.dumps({"data": [{"embedding": self.__class__.embedding_vector}]}).encode(
                "utf-8"
            )
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.end_headers()

    def log_message(self, format: str, *args: Any) -> None:
        return


class _AuthFailureHandler(_OpenAICompatibleHandler):
    def do_POST(self) -> None:  # noqa: N802
        if self.path == "/v1/chat/completions":
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            body = (
                b"Authorization failed for Bearer abc.def.ghi; "
                b"api_key=sk-test-token token=plain-token secret: super-secret "
                b"url=https://user:pass@example.test/v1?token=query-token"
            )
            self.send_response(401)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.end_headers()


class _ProviderStatusHandler(BaseHTTPRequestHandler):
    status_code = 500

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        body = json.dumps(
            {"error": {"message": f"provider status {type(self).status_code}"}}
        ).encode("utf-8")
        self.send_response(type(self).status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        return


class _DroppedConnectionHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802
        self.connection.shutdown(2)
        self.connection.close()

    def log_message(self, format: str, *args: Any) -> None:
        return


def _assert_no_auth_failure_secret_material(text: str) -> None:
    for secret in (
        "abc.def.ghi",
        "sk-test-token",
        "plain-token",
        "super-secret",
        "user:pass",
        "query-token",
    ):
        assert secret not in text


_RUNTIME_ROLE_OUTPUTS: dict[ModelRole, dict[str, Any]] = {
    ModelRole.HIGH_CONFIDENCE_EXTRACTION: {
        "decision": "approved",
        "confidence": 0.9,
        "reason_codes": ["evidence_alignment"],
        "evidence_spans": [{"start": 0, "end": 21, "text": "Backups stay enabled."}],
    },
    ModelRole.PRIVACY_SENSITIVITY: {
        "classification": "normal",
        "reason_codes": ["no_sensitive_indicator"],
        "evidence_spans": [],
    },
    ModelRole.BLOCK_COMPACTION: {
        "summary": None,
        "items": [
            {
                "text": "Backups stay enabled.",
                "source_memory_uuids": ["certification-memory"],
                "source_memory_version_uuids": ["certification-memory-version"],
            }
        ],
        "excluded_memory_version_uuids": [],
        "conflicts": [],
        "status": "ok",
    },
}


def _runtime_role_probe_output(prompt_text: str) -> dict[str, Any] | None:
    """Return a valid runtime-contract output when the probe carries a role marker.

    The three direct StageInvoker roles are certified with the exact runtime
    stage prompt (``ROLE=<role>`` framing), not a prompt-registry id. The output
    is validated against the authoritative runtime contract here so the mock
    provably satisfies the same strict schema and semantic validator runtime uses.
    """

    for role, output in _RUNTIME_ROLE_OUTPUTS.items():
        if f"ROLE={role.value}" in prompt_text:
            contract = runtime_contract_for_role(role)
            assert contract is not None
            contract.validate(output)
            return output
    return None


def _role_contract_probe_output(prompt_text: str) -> dict[str, Any] | None:
    runtime_output = _runtime_role_probe_output(prompt_text)
    if runtime_output is not None:
        return runtime_output
    base: dict[str, Any] = {
        "schema_version": "1.0",
        "prompt_version": "2.0",
        "status": "ok",
        "warnings": [],
        "items": [],
    }
    fixtures: list[tuple[str, dict[str, Any]]] = [
        (
            "memorist.preflight_planning",
            {
                "attachment_mode": "lite",
                "compression_strategy": "none",
                "estimated_tokens": 32,
            },
        ),
        (
            "memorist.import_reconstruction",
            {
                "trust_level": "historical_untrusted",
                "candidate_processing_recommendation": "none",
            },
        ),
        (
            "memorist.unit_analysis",
            {
                "unit_uuid": "certification-unit",
                "memory_signal": "none",
                "evidence": {
                    "quote": "Backups stay enabled.",
                    "span_start": 0,
                    "span_end": 21,
                },
            },
        ),
        (
            "memorist.block_compaction",
            {
                "block_type": "ProjectContextBlock",
                "block_text": "Backups stay enabled.",
                "source_memory_uuids": ["certification-memory"],
                "token_estimate": 8,
                "coverage": {},
            },
        ),
        (
            "memorist.privacy_sensitivity",
            {
                "sensitivity_level": "none",
                "allowed_storage": "allow",
                "allowed_retrieval": "normal",
                "requires_confirmation": False,
            },
        ),
    ]
    for prompt_id, item in fixtures:
        if prompt_id in prompt_text:
            return {**base, "prompt_id": prompt_id, "items": [item]}
    return None


class _HealthCheckHandler(_OpenAICompatibleHandler):
    response_content = json.dumps({"memorist_provider_test": "ok"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path == "/v1/chat/completions":
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            self.__class__.last_payload = payload
            if payload.get("model") != "mock-chat":
                body = json.dumps({"error": {"message": "model not found"}}).encode("utf-8")
                self.send_response(400)
            else:
                body = json.dumps(
                    {"choices": [{"message": {"content": self.response_content}}]}
                ).encode("utf-8")
                self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.end_headers()


@contextmanager
def _served(handler: type[BaseHTTPRequestHandler]) -> Iterator[str]:
    server = HTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        thread.join(timeout=2)


def _create_profile(
    client: TestClient,
    role: str,
    model_name: str,
    supports_embeddings: bool = False,
    cost_profile: dict[str, Any] | None = None,
) -> str:
    payload: dict[str, Any] = {
        "provider_type": "deterministic",
        "model_name": model_name,
        "role": role,
        "supports_embeddings": supports_embeddings,
    }
    if cost_profile is not None:
        payload["cost_profile"] = cost_profile
    response = _assert_ok(client.post("/memcore/model-control/profiles", json=payload))
    return str(response["model_profile_uuid"])


def _create_certified_extraction_profile(
    client: TestClient, endpoint_url: str, model_name: str
) -> str:
    _OpenAICompatibleHandler.reset()
    _OpenAICompatibleHandler.expected_chat_model = model_name
    profile = _assert_ok(
        client.post(
            "/memcore/model-control/profiles",
            json={
                "provider_type": "openai_compatible_llm",
                "provider_name": "test-memory-model",
                "model_name": model_name,
                "role": "memory_extraction",
                "endpoint_url": endpoint_url,
                "supports_json_mode": True,
            },
        )
    )
    profile_uuid = str(profile["model_profile_uuid"])
    _assert_ok(client.post(f"/memcore/model-control/profiles/{profile_uuid}/test", json={}))
    return profile_uuid


@contextmanager
def _db(db_path: Path) -> Iterator[sqlite3.Connection]:
    connection = connect(db_path)
    try:
        apply_migrations(connection)
        yield connection
    finally:
        connection.close()


def _assert_ok(response: Any) -> dict[str, Any]:
    assert response.status_code == 200, response.text
    payload = response.json()
    assert isinstance(payload, dict)
    return payload


def _authorize_turn(db_path: Path, session_uuid: str, message_uuid: str) -> str:
    with _db(db_path) as connection:
        session = connection.execute(
            "SELECT workspace_uuid FROM sessions WHERE session_uuid = ?", (session_uuid,)
        ).fetchone()
        workspace_uuid = str(session["workspace_uuid"])
        MemoryControlRepository(connection, "lite").record_turn_contract(
            input_message_uuid=message_uuid,
            session_uuid=session_uuid,
            workspace_uuid=workspace_uuid,
            user_uuid="test-user",
            chat_uuid=None,
            resolved=ResolvedTurnPolicy(
                policy=normalize_turn_policy("full"),
                source="test",
                attachment_review=False,
            ),
        )
    return workspace_uuid


def test_profile_health_routes_roles_and_provider_types(openai_compatible_server: str) -> None:
    from memcore.model_control.registry import test_profile_health

    llm_roles = [
        "preflight",
        "memory_extraction",
        "import_reconstruction",
        "high_confidence_extraction",
        "block_compaction",
        "privacy_sensitivity",
    ]
    for role in llm_roles:
        _OpenAICompatibleHandler.get_paths = []
        _OpenAICompatibleHandler.post_paths = []
        health = test_profile_health(
            {
                "provider_type": "openai_compatible_llm",
                "model_name": "mock-chat",
                "role": role,
                "endpoint_url": openai_compatible_server,
                "supports_json_mode": True,
            }
        )
        assert health.status == "ok", (role, health.model_dump(mode="json"))
        assert health.provider_type == "openai_compatible_llm"
        expected_calls = 3 if role == "memory_extraction" else 2
        assert _OpenAICompatibleHandler.post_paths == ["/v1/chat/completions"] * expected_calls
        assert _OpenAICompatibleHandler.get_paths == []

    _OpenAICompatibleHandler.post_paths = []
    embedding_health = test_profile_health(
        {
            "provider_type": "openai_compatible_llm",
            "model_name": "mock-embedding",
            "role": "embedding",
            "endpoint_url": openai_compatible_server,
            "embedding_dimension": 3,
        }
    )
    assert embedding_health.status == "ok"
    assert embedding_health.provider_type == "openai_compatible_embedding"
    assert _OpenAICompatibleHandler.post_paths == ["/v1/embeddings"]

    _OpenAICompatibleHandler.post_paths = []
    explicit_embedding_health = test_profile_health(
        {
            "provider_type": "openai_compatible_embedding",
            "model_name": "mock-embedding",
            "role": "memory_extraction",
            "endpoint_url": openai_compatible_server,
            "embedding_dimension": 3,
        }
    )
    assert explicit_embedding_health.status == "error"
    assert explicit_embedding_health.role_probe_status == "incompatible"
    assert explicit_embedding_health.provider_type == "openai_compatible_embedding"
    assert _OpenAICompatibleHandler.post_paths == ["/v1/embeddings"]


def test_profile_health_handles_deterministic_disabled_and_unknown() -> None:
    from memcore.model_control.registry import test_profile_health

    deterministic = test_profile_health(
        {"provider_type": "deterministic", "model_name": "local", "role": "preflight"}
    )
    assert deterministic.status == "ok"
    assert deterministic.local_only_safe is True
    assert deterministic.detail == "deterministic local provider"

    disabled = test_profile_health(
        {
            "provider_type": "openai_compatible_llm",
            "model_name": "disabled-model",
            "role": "preflight",
            "endpoint_url": "http://127.0.0.1:9",
            "is_enabled": False,
        }
    )
    assert disabled.status == "disabled"
    assert disabled.provider_type == "disabled"
    assert disabled.local_only_safe is True

    unknown = test_profile_health(
        {"provider_type": "unknown", "model_name": "mystery", "role": "preflight"}
    )
    assert unknown.status == "error"
    assert unknown.provider_type == "unknown"
    assert unknown.detail == "unknown provider type"


def test_profile_test_persists_unknown_health_event(
    client_and_db: tuple[TestClient, Path],
) -> None:
    client, db_path = client_and_db
    created = _assert_ok(
        client.post(
            "/memcore/model-control/profiles",
            json={
                "provider_type": "unknown",
                "model_name": "mystery",
                "role": "preflight",
            },
        )
    )

    profile_uuid = created["model_profile_uuid"]
    response = _assert_ok(
        client.post(f"/memcore/model-control/profiles/{profile_uuid}/test", json={})
    )

    assert response["health"]["status"] == "error"
    assert response["health"]["provider_type"] == "unknown"
    with _db(db_path) as connection:
        event = connection.execute(
            """
            SELECT status, provider_type, model_name, detail_sanitized
            FROM model_health_events
            WHERE model_profile_uuid = ?
            """,
            (profile_uuid,),
        ).fetchone()
        assert event is not None
        assert event["status"] == "error"
        assert event["provider_type"] == "unknown"
        assert event["model_name"] == "mystery"
        assert event["detail_sanitized"] == "unknown provider type"
