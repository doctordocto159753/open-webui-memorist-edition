from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from memcore.main import create_app
from memcore.storage.migrations import apply_migrations
from memcore.storage.sqlite import connect


def _client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[TestClient, Path]:
    db_path = tmp_path / "pr5b.sqlite"
    monkeypatch.setenv("MEMORIST_RUNTIME_PROFILE", "lite")
    monkeypatch.setenv("MEMORIST_CANONICAL_STORE", "sqlite")
    monkeypatch.setenv("MEMORIST_DB_PATH", str(db_path))
    monkeypatch.setenv("MEMORIST_OBJECT_STORE_PATH", str(tmp_path / "objects"))
    monkeypatch.setenv("MEMORIST_ENV", "test")
    monkeypatch.setenv("MEMORIST_ALLOW_LEGACY_ACTOR_HEADERS_FOR_TESTS", "true")
    return TestClient(create_app()), db_path


def _headers(user: str) -> dict[str, str]:
    return {
        "X-Memorist-User-Id": user,
        "X-Memorist-Workspace-Id": "workspace-1",
    }


def _db(path: Path) -> sqlite3.Connection:
    connection = connect(str(path))
    apply_migrations(connection)
    return connection


def test_chat_workflow_defaults_on_persists_off_and_is_actor_scoped(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    client, db_path = _client(monkeypatch, tmp_path)
    path = "/memcore/memory-control/workflow/chats/shared-chat-id"

    initial = client.get(path, headers=_headers("actor-a"))
    assert initial.status_code == 200
    assert initial.json() == {
        "chat_uuid": "shared-chat-id",
        "session_uuid": None,
        "memory_enabled": True,
        "capture_enabled": True,
        "recall_enabled": True,
        "attachment_enabled": True,
        "state": "on",
        "scope": "chat",
        "source": "system",
        "persisted": False,
        "updated_at": None,
        "regeneration_policy": "original_turn",
    }

    disabled = client.patch(
        path,
        headers=_headers("actor-a"),
        json={"memory_enabled": False},
    )
    assert disabled.status_code == 200
    assert disabled.json()["state"] == "off"
    assert disabled.json()["memory_enabled"] is False
    assert disabled.json()["capture_enabled"] is False
    assert disabled.json()["recall_enabled"] is False
    assert disabled.json()["attachment_enabled"] is False
    assert disabled.json()["persisted"] is True

    assert client.get(path, headers=_headers("actor-a")).json()["state"] == "off"
    other_actor = client.get(path, headers=_headers("actor-b"))
    assert other_actor.status_code == 200
    assert other_actor.json()["state"] == "on"
    assert other_actor.json()["persisted"] is False

    forced_full = client.post(
        "/memcore/memory-control/policy/resolve",
        json={
            "user_uuid": "actor-a",
            "workspace_uuid": "workspace-1",
            "chat_uuid": "shared-chat-id",
            "memorist": {"turn_policy": "full"},
        },
    )
    assert forced_full.status_code == 200
    assert forced_full.json()["policy"] == {
        "mode": "private",
        "capture_enabled": False,
        "recall_enabled": False,
        "attachment_enabled": False,
        "private": True,
    }
    assert forced_full.json()["source"] == "chat"

    with _db(db_path) as connection:
        rows = connection.execute(
            """
            SELECT scope_uuid, turn_policy
            FROM memorist_policy_defaults
            WHERE scope_type = 'chat'
            """
        ).fetchall()
    assert len(rows) == 1
    assert rows[0]["scope_uuid"].startswith("pr5b:")
    assert "actor-a" not in rows[0]["scope_uuid"]
    assert "shared-chat-id" not in rows[0]["scope_uuid"]
    assert rows[0]["turn_policy"] == "private"


def test_workflow_update_requires_boolean_and_can_restore_full(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    client, _ = _client(monkeypatch, tmp_path)
    path = "/memcore/memory-control/workflow/chats/chat-restore"
    assert (
        client.patch(
            path,
            headers=_headers("actor-a"),
            json={"memory_enabled": "false"},
        ).status_code
        == 422
    )

    assert (
        client.patch(
            path,
            headers=_headers("actor-a"),
            json={"memory_enabled": False},
        ).json()["state"]
        == "off"
    )
    restored = client.patch(
        path,
        headers=_headers("actor-a"),
        json={"memory_enabled": True},
    )
    assert restored.status_code == 200
    assert restored.json()["state"] == "on"
    assert restored.json()["capture_enabled"] is True
    assert restored.json()["attachment_enabled"] is True
