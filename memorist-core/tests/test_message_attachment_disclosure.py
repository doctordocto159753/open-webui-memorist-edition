from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from memcore.main import create_app
from memcore.models import utc_now
from memcore.storage.migrations import apply_migrations
from memcore.storage.sqlite import connect

USER = "0be5f480-6f5f-4bf6-9445-1bbf094a1e21"
OTHER_USER = "3b7a2c68-8de3-49a9-b0aa-6e2fd4dc0f9f"
WORKSPACE = "9f0d2f64-40df-4f39-9c30-58c31f8d1a2e"


def _client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[TestClient, Path]:
    db_path = tmp_path / "memorist.sqlite"
    monkeypatch.setenv("MEMORIST_RUNTIME_PROFILE", "lite")
    monkeypatch.setenv("MEMORIST_CANONICAL_STORE", "sqlite")
    monkeypatch.setenv("MEMORIST_DB_PATH", str(db_path))
    monkeypatch.setenv("MEMORIST_OBJECT_STORE_PATH", str(tmp_path / "objects"))
    monkeypatch.setenv("MEMORIST_ENV", "test")
    monkeypatch.setenv("MEMORIST_ALLOW_LEGACY_ACTOR_HEADERS_FOR_TESTS", "true")
    return TestClient(create_app()), db_path


def _headers(user: str = USER, workspace: str = WORKSPACE) -> dict[str, str]:
    return {"x-memorist-user-id": user, "x-memorist-workspace-id": workspace}


def _capture_turn(client: TestClient, *, message_id: str, conversation: str) -> str:
    resolved = client.post(
        "/memcore/openwebui/session/resolve",
        json={
            "openwebui_conversation_id": conversation,
            "user_id": USER,
            "workspace_uuid": WORKSPACE,
        },
    )
    assert resolved.status_code == 200, resolved.text
    session_uuid = resolved.json()["session_uuid"]
    captured = client.post(
        "/memcore/openwebui/messages/capture",
        json={
            "session_uuid": session_uuid,
            "role": "user",
            "content": "My dog is named Alpha and her preferred food is chicken.",
            "openwebui_conversation_id": conversation,
            "openwebui_message_id": message_id,
            "user_id": USER,
            "workspace_uuid": WORKSPACE,
        },
    )
    assert captured.status_code == 200, captured.text
    body = captured.json()
    _insert_attachment(Path(client.app_state_db_path), session_uuid, body["message_uuid"])
    return body["message_uuid"]


def _insert_attachment(db_path: Path, session_uuid: str, input_message_uuid: str) -> None:
    with connect(str(db_path)) as connection:
        apply_migrations(connection)
        connection.execute(
            """
            INSERT INTO memory_context_attachments (
                attachment_uuid, session_uuid, input_message_uuid, attachment_mode,
                ijson_attachment, created_at, owner_user_uuid, workspace_uuid,
                lifecycle_status
            ) VALUES (?, ?, ?, 'standard', '{}', ?, ?, ?, 'prepared')
            """,
            (
                f"attachment-{input_message_uuid}",
                session_uuid,
                input_message_uuid,
                utc_now(),
                USER,
                WORKSPACE,
            ),
        )
        connection.commit()


def _set_lifecycle(db_path: Path, input_message_uuid: str, lifecycle_status: str) -> None:
    with connect(str(db_path)) as connection:
        connection.execute(
            "UPDATE memory_context_attachments SET lifecycle_status = ? WHERE input_message_uuid = ?",
            (lifecycle_status, input_message_uuid),
        )
        connection.commit()


def test_disclosure_states_and_actor_isolation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client, db_path = _client(monkeypatch, tmp_path)
    client.app_state_db_path = db_path  # convenience for helpers
    message_id = "owui-message-1"
    input_message_uuid = _capture_turn(client, message_id=message_id, conversation="chat-1")

    # Prepared attachments stay quiet: no uuid is disclosed.
    prepared = client.get(
        f"/memcore/memory-control/messages/{message_id}/attachment", headers=_headers()
    )
    assert prepared.status_code == 200, prepared.text
    assert prepared.json() == {
        "status": "prepared",
        "attachment_uuid": None,
        "disclose": False,
    }

    _set_lifecycle(db_path, input_message_uuid, "delivered")
    delivered = client.get(
        f"/memcore/memory-control/messages/{message_id}/attachment", headers=_headers()
    ).json()
    assert delivered["disclose"] is True
    assert delivered["status"] == "delivered"
    assert delivered["attachment_uuid"] == f"attachment-{input_message_uuid}"

    _set_lifecycle(db_path, input_message_uuid, "used_for_response")
    used = client.get(
        f"/memcore/memory-control/messages/{message_id}/attachment", headers=_headers()
    ).json()
    assert used["disclose"] is True
    assert used["status"] == "used_for_response"

    # A different actor must not see another user's turn state.
    foreign = client.get(
        f"/memcore/memory-control/messages/{message_id}/attachment",
        headers=_headers(user=OTHER_USER),
    ).json()
    assert foreign == {"status": "none", "attachment_uuid": None, "disclose": False}

    # A different workspace must not see it either.
    other_workspace = client.get(
        f"/memcore/memory-control/messages/{message_id}/attachment",
        headers=_headers(workspace="7d34cf3e-5be1-4f9e-a2fd-2f45f6f9f19d"),
    ).json()
    assert other_workspace == {"status": "none", "attachment_uuid": None, "disclose": False}


def test_unknown_message_is_quiet(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    client, _ = _client(monkeypatch, tmp_path)
    response = client.get(
        "/memcore/memory-control/messages/never-captured/attachment", headers=_headers()
    )
    assert response.status_code == 200
    assert response.json() == {"status": "none", "attachment_uuid": None, "disclose": False}
