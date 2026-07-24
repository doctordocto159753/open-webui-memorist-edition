from __future__ import annotations

import pytest

from memcore.config import Settings
from memcore.memory_worker.postgres.pipeline import _scope_for_message
from memcore.memory_worker.service import MemoryJobWorkerService, _job_payload


def test_memory_worker_is_enabled_for_lite_sqlite() -> None:
    service = MemoryJobWorkerService(Settings(enable_memory_worker=True))

    assert service.enabled is True


def test_memory_worker_is_disabled_when_feature_flag_is_off() -> None:
    service = MemoryJobWorkerService(Settings(enable_memory_worker=False))

    service.start()

    assert service.enabled is False
    assert service.process_once() is False
    assert service._thread is None


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        (
            {"session_uuid": "session", "workspace_uuid": "workspace", "project_uuid": "project"},
            ("project", "project"),
        ),
        (
            {"session_uuid": "session", "workspace_uuid": "workspace", "project_uuid": None},
            ("workspace", "workspace"),
        ),
        (
            {"session_uuid": "session", "workspace_uuid": None, "project_uuid": None},
            ("session", "session"),
        ),
    ],
)
def test_full_postgres_memory_scope_matches_shared_scope_precedence(
    message: dict[str, str | None], expected: tuple[str, str]
) -> None:
    assert _scope_for_message(message) == expected


def test_memory_worker_accepts_jsonb_mapping_or_serialized_payload() -> None:
    assert _job_payload({"payload_jsonb": {"message_uuid": "message"}}) == {
        "message_uuid": "message"
    }
    assert _job_payload({"payload_jsonb": '{"message_uuid":"message"}'}) == {
        "message_uuid": "message"
    }
    assert _job_payload({"payload_ijson": '{"message_uuid":"message"}'}) == {
        "message_uuid": "message"
    }


def test_memory_worker_rejects_job_without_message_identity() -> None:
    with pytest.raises(ValueError, match="missing message_uuid"):
        _job_payload({"payload_jsonb": {}})
