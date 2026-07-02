from __future__ import annotations

import sqlite3
import sys
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from memcore.attachments.budget import compute_attachment_budget
from memcore.main import create_app
from memcore.models import JobStatus
from memcore.repositories import JobRepository
from memcore.storage.migrations import apply_migrations
from memcore.storage.sqlite import connect, with_busy_retry

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from release.scan_forbidden_files import scan_path  # type: ignore[import-not-found] # noqa: E402


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> TestClient:
    monkeypatch.setenv("MEMORIST_DB_PATH", str(tmp_path / "memorist.sqlite"))
    monkeypatch.setenv("MEMORIST_OBJECT_STORE_PATH", str(tmp_path / "objects"))
    return TestClient(create_app())


def test_openapi_alias_is_reachable_with_testclient(client: TestClient) -> None:
    response = client.get("/openapi.json")

    assert response.status_code == 200
    assert response.json()["info"]["title"] == "memorist-core"


def test_temp_chat_alias_promotes_to_stable_chat_id(client: TestClient) -> None:
    first = client.post(
        "/memcore/openwebui/session/resolve",
        json={
            "temporary_chat_id": "tmp-chat-1",
            "client_session_nonce": "nonce-1",
            "first_message_hash": "hash-1",
            "user_id": "user-1",
            "created_at": "2026-06-26T10:05:00Z",
        },
    )
    assert first.status_code == 200
    session_uuid = first.json()["session_uuid"]

    second = client.post(
        "/memcore/openwebui/session/resolve",
        json={
            "openwebui_conversation_id": "stable-chat-1",
            "client_session_nonce": "nonce-1",
            "first_message_hash": "hash-1",
            "user_id": "user-1",
            "created_at": "2026-06-26T10:06:00Z",
        },
    )

    assert second.status_code == 200
    assert second.json()["session_uuid"] == session_uuid
    alias_types = {alias["alias_type"] for alias in second.json()["aliases"]}
    assert {"openwebui_temp_chat_id", "client_session_nonce", "openwebui_chat_id"}.issubset(
        alias_types
    )


def test_stable_id_later_resolves_no_chat_first_message_by_nonce(client: TestClient) -> None:
    first = client.post(
        "/memcore/openwebui/session/resolve",
        json={
            "client_session_nonce": "nonce-no-chat",
            "first_message_hash": "hash-no-chat",
            "user_id": "user-1",
        },
    )
    session_uuid = first.json()["session_uuid"]

    second = client.post(
        "/memcore/openwebui/session/resolve",
        json={
            "openwebui_conversation_id": "stable-later",
            "client_session_nonce": "nonce-no-chat",
            "first_message_hash": "hash-no-chat",
            "user_id": "user-1",
        },
    )

    assert second.json()["session_uuid"] == session_uuid


def test_alias_does_not_cross_user_boundary(client: TestClient) -> None:
    first = client.post(
        "/memcore/openwebui/session/resolve",
        json={"openwebui_conversation_id": "shared-chat-id", "user_id": "user-a"},
    )
    second = client.post(
        "/memcore/openwebui/session/resolve",
        json={"openwebui_conversation_id": "shared-chat-id", "user_id": "user-b"},
    )

    assert first.json()["session_uuid"] != second.json()["session_uuid"]


def test_capture_dedup_uses_backend_key(client: TestClient) -> None:
    session = client.post(
        "/memcore/openwebui/session/resolve",
        json={"openwebui_conversation_id": "chat-dedup", "user_id": "user-1"},
    ).json()
    payload = {
        "session_uuid": session["session_uuid"],
        "openwebui_conversation_id": "chat-dedup",
        "openwebui_message_id": "message-1",
        "source_message_id": "message-1",
        "turn_index": 1,
        "timestamp": "2026-06-26T10:00:00Z",
        "role": "user",
        "content": "hello",
    }

    first = client.post("/memcore/openwebui/messages/capture", json=payload)
    second = client.post("/memcore/openwebui/messages/capture", json=payload)

    assert first.json()["duplicate"] is False
    assert second.json()["duplicate"] is True


def test_session_lineage_endpoint_lists_aliases(client: TestClient) -> None:
    session = client.post(
        "/memcore/openwebui/session/resolve",
        json={
            "openwebui_conversation_id": "lineage-chat",
            "client_session_nonce": "lineage-nonce",
            "user_id": "user-1",
        },
    ).json()

    lineage = client.get(f"/memcore/openwebui/sessions/{session['session_uuid']}/lineage")

    assert lineage.status_code == 200
    assert {alias["alias_type"] for alias in lineage.json()["aliases"]} >= {
        "openwebui_chat_id",
        "client_session_nonce",
    }


def test_dynamic_budget_small_context_disables_attachment() -> None:
    decision = compute_attachment_budget(
        mode="standard",
        requested_budget=1800,
        target_model="tiny",
        model_context_window=2048,
        recent_conversation_text=None,
        estimated_recent_conversation_tokens=900,
        max_tokens=1800,
        context_ratio=0.10,
        min_tokens=256,
        reserved_completion_tokens=1024,
        fallback_context_window=8192,
        safety_margin_tokens=256,
    )

    assert decision.effective_token_budget == 0
    assert decision.attachment_mode == "disabled"


def test_dynamic_budget_long_context_respects_cap() -> None:
    decision = compute_attachment_budget(
        mode="full",
        requested_budget=None,
        target_model="long-context",
        model_context_window=128000,
        recent_conversation_text=None,
        estimated_recent_conversation_tokens=1000,
        max_tokens=1800,
        context_ratio=0.10,
        min_tokens=256,
        reserved_completion_tokens=1024,
        fallback_context_window=8192,
        safety_margin_tokens=256,
    )

    assert decision.effective_token_budget == 1800
    assert decision.attachment_mode == "full"


def test_dynamic_budget_unknown_model_uses_fallback_warning() -> None:
    decision = compute_attachment_budget(
        mode="standard",
        requested_budget=None,
        target_model=None,
        model_context_window=None,
        recent_conversation_text="hello",
        estimated_recent_conversation_tokens=None,
        max_tokens=1800,
        context_ratio=0.10,
        min_tokens=256,
        reserved_completion_tokens=1024,
        fallback_context_window=8192,
        safety_margin_tokens=256,
    )

    assert decision.effective_token_budget > 0
    assert "missing_model_context_window_used_fallback" in decision.warnings


def test_sqlite_pragmas_are_applied(tmp_path: Path) -> None:
    connection = connect(tmp_path / "pragma.sqlite")
    try:
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    finally:
        connection.close()


def test_busy_retry_succeeds_after_transient_locked_errors() -> None:
    attempts = {"count": 0}

    def operation() -> str:
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise sqlite3.OperationalError("database is locked")
        return "ok"

    assert with_busy_retry(operation, max_attempts=4, base_delay_seconds=0.001) == "ok"
    assert attempts["count"] == 3


def test_busy_retry_eventually_fails() -> None:
    def operation() -> str:
        raise sqlite3.OperationalError("database is locked")

    with pytest.raises(sqlite3.OperationalError):
        with_busy_retry(operation, max_attempts=2, base_delay_seconds=0.001)


def test_atomic_job_claim_allows_only_one_worker(tmp_path: Path) -> None:
    db_path = tmp_path / "jobs.sqlite"
    setup = connect(db_path)
    apply_migrations(setup)
    JobRepository(setup).enqueue_job("memory_worker", {"message_uuid": "m1"}, priority=50)
    setup.close()

    claimed: list[str] = []
    lock = threading.Lock()

    def worker() -> None:
        connection = connect(db_path)
        try:
            job = JobRepository(connection).claim_next_job()
            if job is not None:
                with lock:
                    claimed.append(job.job_uuid)
        finally:
            connection.close()

    threads = [threading.Thread(target=worker) for _ in range(5)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(claimed) == 1


def test_job_claim_respects_priority(tmp_path: Path) -> None:
    connection = connect(tmp_path / "priority.sqlite")
    apply_migrations(connection)
    low = JobRepository(connection).enqueue_job("low", {"n": 1}, priority=1)
    high = JobRepository(connection).enqueue_job("high", {"n": 2}, priority=99)

    claimed = JobRepository(connection).claim_next_job()

    assert claimed is not None
    assert claimed.job_uuid == high.job_uuid
    assert claimed.status == JobStatus.RUNNING
    assert low.job_uuid != claimed.job_uuid
    connection.close()


def test_forbidden_scanner_flags_artifacts_and_secrets(tmp_path: Path) -> None:
    bad_dir = tmp_path / "package"
    (bad_dir / "__pycache__").mkdir(parents=True)
    (bad_dir / "__pycache__" / "x.pyc").write_bytes(b"bad")
    leaked_value = "OPENAI_" + "API_KEY=" + "sk-" + "testsecret000000"
    (bad_dir / "leak.txt").write_text(leaked_value, encoding="utf-8")

    issues = scan_path(bad_dir)

    reasons = {issue.reason for issue in issues}
    assert "forbidden path part" in reasons
    assert any("secret-like content" in reason for reason in reasons)
