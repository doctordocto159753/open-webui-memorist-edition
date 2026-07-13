from __future__ import annotations

import base64
import hmac
import json
import os
import time
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from memcore.config import get_settings
from memcore.main import create_app

SECRET = "actor-assertion-test-secret-with-sufficient-entropy"
SERVICE_TOKEN = "internal-service-test-token-with-sufficient-entropy"


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def _assertion(
    method: str,
    path: str,
    *,
    user: str = "actor-user",
    workspace: str = "actor-workspace",
    issuer: str = "openwebui-backend",
    audience: str = "memorist-core",
    issued_at: int | None = None,
    expires_at: int | None = None,
    nonce: str | None = None,
    secret: str = SECRET,
) -> str:
    now = int(time.time())
    claims = {
        "sub": user,
        "workspace_uuid": workspace,
        "iss": issuer,
        "aud": audience,
        "iat": issued_at if issued_at is not None else now,
        "exp": expires_at if expires_at is not None else now + 30,
        "purpose": f"{method}:{path}",
        "nonce": nonce or uuid4().hex,
    }
    encoded = _b64(json.dumps(claims, sort_keys=True, separators=(",", ":")).encode())
    signature = _b64(hmac.new(secret.encode(), encoded.encode(), sha256).digest())
    return f"{encoded}.{signature}"


def _client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> TestClient:
    runtime = os.getenv("MEMORIST_AUTH_TEST_RUNTIME", "lite")
    monkeypatch.setenv("MEMORIST_ENV", "test")
    monkeypatch.setenv("MEMORIST_ALLOW_LEGACY_ACTOR_HEADERS_FOR_TESTS", "false")
    monkeypatch.setenv("MEMORIST_ACTOR_ASSERTION_SECRET", SECRET)
    monkeypatch.setenv("MEMORIST_ACTOR_SERVICE_TOKEN", SERVICE_TOKEN)
    monkeypatch.setenv("MEMORIST_RUNTIME_PROFILE", runtime)
    monkeypatch.setenv("MEMORIST_OBJECT_STORE_PATH", str(tmp_path / "objects"))
    if runtime == "full":
        monkeypatch.setenv("MEMORIST_CANONICAL_STORE", "postgres")
        monkeypatch.setenv("MEMORIST_ALLOW_FULL_GRAPH_DEGRADED", "true")
        monkeypatch.setenv("MEMORIST_HOT_SCHEDULER", "in_memory")
        if not os.getenv("MEMORIST_POSTGRES_DSN"):
            raise AssertionError("Full authentication certification requires PostgreSQL")
    else:
        monkeypatch.setenv("MEMORIST_CANONICAL_STORE", "sqlite")
        monkeypatch.setenv("MEMORIST_DB_PATH", str(tmp_path / "actor.sqlite3"))
    get_settings.cache_clear()
    return TestClient(create_app())


def _headers(token: str, *, service: str = SERVICE_TOKEN) -> dict[str, str]:
    return {
        "X-Memorist-Service-Token": service,
        "X-Memorist-Actor-Assertion": token,
    }


def test_signed_actor_boundary_and_replay_protection(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client = _client(monkeypatch, tmp_path)
    path = "/memcore/memory-control/policy/resolve"
    body = {
        "user_uuid": "actor-user",
        "workspace_uuid": "actor-workspace",
        "memorist": {"turn_policy": "full"},
    }
    token = _assertion("POST", path, nonce=f"valid-{uuid4().hex}")
    assert client.post(path, json=body, headers=_headers(token)).status_code == 200
    assert client.post(path, json=body, headers=_headers(token)).status_code == 409
    assert client.post(path, json=body).status_code == 401
    assert (
        client.post(
            path,
            json=body,
            headers={
                "X-Memorist-User-Id": "actor-user",
                "X-Memorist-Workspace-Id": "actor-workspace",
            },
        ).status_code
        == 401
    )
    assert client.post(path, json=body, headers=_headers(token, service="wrong")).status_code == 401


def test_actor_claim_validation_and_scope_mismatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client = _client(monkeypatch, tmp_path)
    path = "/memcore/memory-control/policy/resolve"
    body = {
        "user_uuid": "actor-user",
        "workspace_uuid": "actor-workspace",
        "memorist": {},
    }
    now = int(time.time())
    rejected = (
        _assertion("POST", path, secret="wrong-signing-secret"),
        _assertion("POST", path, expires_at=now - 1),
        _assertion("POST", path, audience="wrong-audience"),
        _assertion("POST", path, issuer="wrong-issuer"),
    )
    for token in rejected:
        assert client.post(path, json=body, headers=_headers(token)).status_code == 401
    user_mismatch = {**body, "user_uuid": "other-user"}
    workspace_mismatch = {**body, "workspace_uuid": "other-workspace"}
    assert (
        client.post(
            path,
            json=user_mismatch,
            headers=_headers(_assertion("POST", path)),
        ).status_code
        == 403
    )
    assert (
        client.post(
            path,
            json=workspace_mismatch,
            headers=_headers(_assertion("POST", path)),
        ).status_code
        == 403
    )
