from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from memcore.config import Settings
from memcore.main import create_app
from memcore.memory_worker.pipeline import MemoryWorkerPipeline
from memcore.model_control.providers.openai_compatible import (
    OpenAICompatibleEmbeddingProvider,
    OpenAICompatibleLLMProvider,
)
from memcore.model_control.repository import ModelControlRepository
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


    assert health.detail == "HTTP 200"
    assert handler.last_payload["response_format"] == {"type": "json_object"}


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
    accepted = _assert_ok(
        client.post(
            "/memcore/model-control/defaults",
            json={
                "role": "memory_extraction",
                "model_profile_uuid": created["model_profile_uuid"],
            },
        )
    )
    assert accepted["model_profile_uuid"] == created["model_profile_uuid"]

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


def test_preflight_provider_fail_open(client_and_db: tuple[TestClient, Path]) -> None:
    client, _db_path = client_and_db
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

    response = _assert_ok(
        client.post(
            "/memcore/preflight",
            json={
                "session_uuid": session["session_uuid"],
                "input_message_uuid": message["message_uuid"],
                "retrieval_mode": "invalid-mode",
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
            "/memcore/model-control/defaults",
            json={"role": "preflight", "model_profile_uuid": profile["model_profile_uuid"]},
        )
    )
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

    response = _assert_ok(
        client.post(
            "/memcore/preflight",
            json={
                "session_uuid": session["session_uuid"],
                "input_message_uuid": message["message_uuid"],
                "recent_conversation_text": "Remember that this project uses a role matrix.",
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
) -> None:
    client, db_path = client_and_db
    session = _assert_ok(
        client.post(
            "/memcore/openwebui/session/resolve",
            json={"openwebui_conversation_id": "chat-model-control", "title": "Chat"},
        )
    )
    chat_profile = _create_profile(client, "main_chat_observed", "openwebui-chat-model")
    extraction_profile = _create_profile(client, "memory_extraction", "memory-extractor")
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
    tmp_path: Path,
) -> None:
    client, db_path = client_and_db
    extraction_profile = _create_profile(client, "memory_extraction", "worker-extractor")
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
    surfaces = (ROOT / "open-webui-integration/memorist/ui/surfaces.ts").read_text(
        encoding="utf-8"
    )
    client = (ROOT / "open-webui-integration/memorist/ui/memoristClient.ts").read_text(
        encoding="utf-8"
    )
    processing_nodes = (
        ROOT / "open-webui-integration/memorist/ui/processingNodes.ts"
    ).read_text(encoding="utf-8")

    assert "main_chat_observed" in model_control
    assert "memory_extraction" in model_control
    assert "FIRST_RUN_MODEL_DEFAULTS" in model_control
    assert "ROLE_MATRIX_COLUMNS" in model_control
    assert "ModelControlTab" in surfaces
    assert "Configure Memorist model roles" in surfaces
    assert "modelControlProfiles" in client
    assert "/model-control/privacy" in client
    assert "/costs/model-roles" in client
    assert "/settings/memorist/processing-nodes" in processing_nodes
    assert "customElements.define" in processing_nodes
    assert "acknowledgeModelControlPrivacy" in processing_nodes


def test_docs_include_model_roles() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    architecture = (ROOT / "docs/architecture.md").read_text(encoding="utf-8")

    assert "Model Control Plane" in readme
    assert "main_chat_observed" in readme
    assert "memory_extraction" in readme
    assert "privacy acknowledgement" in readme
    assert "Model Control Plane path" in architecture
    assert "embedding" in architecture


def test_openai_compatible_health_check_uses_chat_completions_not_models(
    openai_compatible_server: str,
) -> None:
    _OpenAICompatibleHandler.get_paths = []
    _OpenAICompatibleHandler.post_paths = []

    health = OpenAICompatibleLLMProvider(
        openai_compatible_server,
        "mock-chat",
        supports_json_mode=True,
    ).health_check()

    assert health.status == "ok"
    assert _OpenAICompatibleHandler.get_paths == []
    assert _OpenAICompatibleHandler.post_paths == ["/v1/chat/completions"]


def test_openai_compatible_embedding_health_check_uses_embeddings(
    openai_compatible_server: str,
) -> None:
    _OpenAICompatibleHandler.get_paths = []
    _OpenAICompatibleHandler.post_paths = []
    _OpenAICompatibleHandler.last_payload = {}

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
    _OpenAICompatibleHandler.post_paths = []

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


def test_openai_compatible_health_check_reports_wrong_model() -> None:
    with _served(_HealthCheckHandler) as endpoint_url:
        health = OpenAICompatibleLLMProvider(endpoint_url, "wrong-chat").health_check()

    assert health.status == "error"
    assert health.model_name == "wrong-chat"
    assert health.detail is not None
    assert "HTTP 400" in health.detail


@pytest.fixture
def openai_compatible_server() -> Iterator[str]:
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

    def do_GET(self) -> None:  # noqa: N802
        self.__class__.get_paths.append(self.path)
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
        if self.path == "/v1/chat/completions":
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            self.__class__.last_payload = payload
            if payload.get("model") != "mock-chat":
                body = json.dumps({"error": {"message": "model not found"}}).encode("utf-8")
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
            return
        if self.path == "/v1/embeddings":
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            self.__class__.last_payload = payload
            if payload.get("model") != "mock-embedding":
                body = json.dumps({"error": {"message": "model not found"}}).encode("utf-8")
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            body = json.dumps({"data": [{"embedding": [0.1, 0.2, 0.3]}]}).encode("utf-8")
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
        assert health.status == "ok"
        assert health.provider_type == "openai_compatible_llm"
        assert _OpenAICompatibleHandler.post_paths == ["/v1/chat/completions"]
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
    assert explicit_embedding_health.status == "ok"
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
