from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from memcore.main import create_app


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> TestClient:
    monkeypatch.setenv("MEMORIST_RUNTIME_PROFILE", "lite")
    monkeypatch.setenv("MEMORIST_CANONICAL_STORE", "sqlite")
    monkeypatch.setenv("MEMORIST_DB_PATH", str(tmp_path / "pr5c.sqlite"))
    monkeypatch.setenv("MEMORIST_OBJECT_STORE_PATH", str(tmp_path / "objects"))
    monkeypatch.setenv("MEMORIST_ENV", "test")
    monkeypatch.setenv("MEMORIST_ALLOW_LEGACY_ACTOR_HEADERS_FOR_TESTS", "true")
    return TestClient(create_app())


def test_setup_status_is_ready_with_truthful_local_fallback(client: TestClient) -> None:
    response = client.get(
        "/memcore/model-control/setup/status",
        params={"workspace_uuid": "workspace-1"},
    )
    assert response.status_code == 200
    payload = response.json()

    assert payload["memory_setup_required"] is False
    assert payload["ready_for_memory_processing"] is True
    assert payload["recommended_setup"] is True
    assert payload["local_fallback_available"] is True
    assert payload["configured_roles"] == []
    assert "memory_extraction" in payload["fallback_roles"]
    assert "high_confidence_extraction" in payload["fallback_roles"]
    assert payload["missing_roles"] == []
    assert payload["secret_strategy"] == "env_var_reference"
    assert payload["secret_values_returned"] is False


@pytest.mark.parametrize(
    ("role", "model_name"),
    [
        ("memory_extraction", "deterministic_extraction"),
        ("high_confidence_extraction", "deterministic_high_confidence"),
    ],
)
def test_role_profile_can_be_configured_and_resolved_separately(
    client: TestClient,
    role: str,
    model_name: str,
) -> None:
    created = client.post(
        "/memcore/model-control/profiles",
        json={
            "profile_name": f"{role} local",
            "provider_type": "deterministic",
            "provider_name": "Local deterministic",
            "model_name": model_name,
            "role": role,
            "secret_strategy": "none",
            "endpoint_is_local": True,
        },
    )
    assert created.status_code == 200, created.text
    profile_uuid = created.json()["model_profile_uuid"]

    selected = client.post(
        "/memcore/model-control/defaults",
        json={
            "role": role,
            "model_profile_uuid": profile_uuid,
        },
    )
    assert selected.status_code == 200, selected.text

    status = client.get(
        "/memcore/model-control/setup/status",
        params={"workspace_uuid": "workspace-1"},
    )
    assert status.status_code == 200
    role_status = next(item for item in status.json()["roles"] if item["role"] == role)
    assert role_status["configured"] is True
    assert role_status["source"] == "configured_default"
    assert role_status["model_profile_uuid"] == profile_uuid
    assert role_status["provider_type"] == "deterministic"


def test_profile_summary_never_returns_secret_value_or_reference(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret_value = "test-provider-value-not-for-output"
    env_name = "MEMORIST_PR5C_TEST_PROVIDER_KEY"
    monkeypatch.setenv(env_name, secret_value)

    created = client.post(
        "/memcore/model-control/profiles",
        json={
            "profile_name": "Custom OpenAI-compatible extraction",
            "provider_type": "openai_compatible_llm",
            "provider_name": "Custom OpenAI-compatible endpoint",
            "model_name": "example-memory-model",
            "role": "memory_extraction",
            "endpoint_url": "http://127.0.0.1:9/v1",
            "endpoint_is_local": True,
            "secret_strategy": "env_var",
            "secret_env_var_name": env_name,
            "supports_json_mode": True,
        },
    )
    assert created.status_code == 200, created.text
    payload_text = created.text
    assert created.json()["secret_configured"] is True
    assert secret_value not in payload_text
    assert env_name not in payload_text

    fetched = client.get(
        f"/memcore/model-control/profiles/{created.json()['model_profile_uuid']}"
    )
    assert fetched.status_code == 200
    assert secret_value not in fetched.text
    assert env_name not in fetched.text


def test_invalid_provider_endpoint_and_raw_secret_metadata_are_rejected(
    client: TestClient,
) -> None:
    endpoint_secret = client.post(
        "/memcore/model-control/profiles",
        json={
            "provider_type": "openai_compatible_llm",
            "model_name": "example",
            "role": "memory_extraction",
            "endpoint_url": "https://example.test/v1?api_key=forbidden",
        },
    )
    assert endpoint_secret.status_code == 422
    assert "forbidden" not in endpoint_secret.text

    metadata_secret = client.post(
        "/memcore/model-control/profiles",
        json={
            "provider_type": "deterministic",
            "model_name": "local",
            "role": "memory_extraction",
            "metadata": {"api_key": "forbidden"},
        },
    )
    assert metadata_secret.status_code == 422
    assert "forbidden" not in metadata_secret.text

