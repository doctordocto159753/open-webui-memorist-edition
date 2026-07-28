from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import pytest

from memcore.config import Settings
from memcore.memory_worker.fencing import LeaseFenceRejected
from memcore.memory_worker.pipeline import MemoryWorkerPipeline
from memcore.memory_worker.postgres.pipeline import _scope_for_message
from memcore.memory_worker.prepared import PreparedJakobsonInference
from memcore.memory_worker.service import (
    JobLeaseFence,
    MemoryJobWorkerService,
    _claim_next_extraction_job_sqlite,
    _job_payload,
    _record_failure_sqlite,
)
from memcore.model_control.providers.base import ProviderHealth
from memcore.model_control.repository import ModelControlRepository
from memcore.model_control.role_contracts import role_contract_manifest_hash
from memcore.model_control.schemas import ModelProfileCreate, ProviderType
from memcore.models import ModelRole
from memcore.repositories import JobRepository, MessageRepository, SessionRepository
from memcore.storage.migrations import apply_migrations
from memcore.storage.sqlite import connect

pytestmark = pytest.mark.usefixtures("wp02_downstream_semantic_model")


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


def test_reclaimed_worker_fences_late_worker_from_all_canonical_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "stale-worker.sqlite"
    settings = Settings(
        db_path=str(db_path),
        object_store_path=str(tmp_path / "objects"),
        enable_memory_worker=True,
        memory_worker_lease_seconds=60,
    )
    setup = connect(db_path)
    apply_migrations(setup)
    session = SessionRepository(setup).create_session()
    message = MessageRepository(setup).create_message(
        session.session_uuid,
        role="user",
        creator_type="user",
        raw_text="Do not disable backups.",
    )
    job = JobRepository(setup).enqueue_job_once(
        "memory_extraction",
        {"message_uuid": message.message_uuid},
    )
    setup.commit()
    setup.close()

    original_prepare = MemoryWorkerPipeline.prepare_message
    entered = threading.Event()
    release = threading.Event()

    def blocked_prepare(
        self: MemoryWorkerPipeline,
        message_uuid: str,
        model_target: dict[str, object] | None = None,
    ) -> PreparedJakobsonInference:
        if threading.current_thread().name == "stale-worker-a":
            entered.set()
            assert release.wait(timeout=10)
        return original_prepare(self, message_uuid, model_target)

    monkeypatch.setattr(MemoryWorkerPipeline, "prepare_message", blocked_prepare)
    service = MemoryJobWorkerService(settings)
    worker_a = threading.Thread(
        target=lambda: service.process_once("worker-a"),
        name="stale-worker-a",
        daemon=True,
    )
    worker_a.start()
    assert entered.wait(timeout=5)

    reclaim = connect(db_path)
    reclaim.execute(
        "UPDATE jobs SET lease_expires_at = '2000-01-01T00:00:00' WHERE job_uuid = ?",
        (job.job_uuid,),
    )
    reclaim.commit()
    reclaim.close()
    assert service.process_once("worker-b") is True

    after_worker_b = connect(db_path)
    counts_after_worker_b = _canonical_counts(after_worker_b)
    completed = after_worker_b.execute(
        """
        SELECT status, locked_by, lease_token, lease_generation, attempts
        FROM jobs WHERE job_uuid = ?
        """,
        (job.job_uuid,),
    ).fetchone()
    after_worker_b.close()
    assert dict(completed) == {
        "status": "succeeded",
        "locked_by": None,
        "lease_token": None,
        "lease_generation": 2,
        "attempts": 2,
    }

    release.set()
    worker_a.join(timeout=10)
    assert not worker_a.is_alive()

    verify = connect(db_path)
    assert _canonical_counts(verify) == counts_after_worker_b
    final_job = verify.execute(
        "SELECT status, locked_by, lease_token, lease_generation FROM jobs WHERE job_uuid = ?",
        (job.job_uuid,),
    ).fetchone()
    assert dict(final_job) == {
        "status": "succeeded",
        "locked_by": None,
        "lease_token": None,
        "lease_generation": 2,
    }
    assert counts_after_worker_b["memory_processing_runs"] == 1
    assert counts_after_worker_b["memory_candidates"] >= 1
    assert counts_after_worker_b["memories"] >= 1
    verify.close()


def test_job_lease_fence_renews_and_rejects_token_mismatch(tmp_path: Path) -> None:
    connection, job, fence, _ = _leased_job(tmp_path, "renew")
    original_expiry = str(job["lease_expires_at"])

    fence(before_provider=True)
    connection.commit()
    renewed_expiry = str(
        connection.execute(
            "SELECT lease_expires_at FROM jobs WHERE job_uuid = ?",
            (job["job_uuid"],),
        ).fetchone()["lease_expires_at"]
    )
    assert renewed_expiry >= original_expiry

    connection.execute(
        "UPDATE jobs SET lease_token = 'different-token' WHERE job_uuid = ?",
        (job["job_uuid"],),
    )
    connection.commit()
    with pytest.raises(LeaseFenceRejected, match="no longer owned"):
        fence()
    connection.close()


def test_cancelled_job_cannot_be_overwritten_by_stale_failure(tmp_path: Path) -> None:
    connection, job, fence, _ = _leased_job(tmp_path, "cancel")
    connection.execute(
        "UPDATE jobs SET status = 'cancelled' WHERE job_uuid = ?",
        (job["job_uuid"],),
    )
    connection.commit()

    with pytest.raises(LeaseFenceRejected, match="no longer owned"):
        fence()
    _record_failure_sqlite(connection, job, "worker-a", RuntimeError("late error"))
    status = connection.execute(
        "SELECT status FROM jobs WHERE job_uuid = ?",
        (job["job_uuid"],),
    ).fetchone()["status"]
    assert status == "cancelled"
    connection.close()


def test_source_deletion_during_provider_invalidates_lease_fence(tmp_path: Path) -> None:
    connection, _, fence, message_uuid = _leased_job(tmp_path, "source-delete")
    connection.execute("DELETE FROM messages WHERE message_uuid = ?", (message_uuid,))
    connection.commit()

    with pytest.raises(LeaseFenceRejected, match="source no longer exists"):
        fence()
    connection.close()


def test_role_default_change_during_provider_invalidates_lease_fence(
    tmp_path: Path,
) -> None:
    connection, _, fence, _ = _leased_job(tmp_path, "profile-change")
    repository = ModelControlRepository(connection)
    profile = repository.create_profile(
        ModelProfileCreate(
            profile_name="Changed extractor",
            provider_type=ProviderType.DETERMINISTIC,
            provider_name="test deterministic",
            model_name="changed-extractor",
            role=ModelRole.MEMORY_EXTRACTION,
            endpoint_is_local=True,
            supports_structured_output=True,
            supports_json_mode=True,
            privacy_acknowledged=True,
        )
    )
    repository.record_health_event(
        profile.model_profile_uuid,
        ProviderHealth(
            status="ok",
            provider_type=profile.provider_type,
            model_name=profile.model_name,
            latency_ms=1,
            local_only_safe=True,
            role_compatibility_status="compatible",
            overall_status="ok",
            role_manifest_hash=role_contract_manifest_hash(profile.role),
            role_probe_status="compatible",
        ),
    )
    repository.set_default(ModelRole.MEMORY_EXTRACTION, profile.model_profile_uuid)
    connection.commit()

    with pytest.raises(LeaseFenceRejected, match="profile changed"):
        fence()
    connection.close()


def _canonical_counts(connection: Any) -> dict[str, int]:
    tables = (
        "memory_processing_runs",
        "prompt_execution_runs",
        "processing_stage_runs",
        "memory_candidates",
        "memories",
        "memory_versions",
        "memory_version_embeddings",
        "embedding_outbox",
        "graph_projection_outbox",
        "memory_block_versions",
    )
    return {
        table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        for table in tables
    }


def _leased_job(
    tmp_path: Path,
    name: str,
) -> tuple[Any, dict[str, Any], JobLeaseFence, str]:
    db_path = tmp_path / f"{name}.sqlite"
    settings = Settings(
        db_path=str(db_path),
        object_store_path=str(tmp_path / f"{name}-objects"),
        memory_worker_lease_seconds=60,
    )
    connection = connect(db_path)
    apply_migrations(connection)
    session = SessionRepository(connection).create_session()
    message = MessageRepository(connection).create_message(
        session.session_uuid,
        role="user",
        creator_type="user",
        raw_text="Do not disable backups.",
    )
    JobRepository(connection).enqueue_job_once(
        "memory_extraction",
        {"message_uuid": message.message_uuid},
    )
    connection.commit()
    job = _claim_next_extraction_job_sqlite(connection, "worker-a", 60)
    assert job is not None
    pipeline = MemoryWorkerPipeline(connection, settings)
    expected_snapshot = pipeline.execution_snapshot(message.message_uuid)
    fence = JobLeaseFence(
        connection,
        job=job,
        worker_id="worker-a",
        message_uuid=message.message_uuid,
        lease_seconds=60,
        expected_snapshot=expected_snapshot,
        current_snapshot=lambda: pipeline.execution_snapshot(message.message_uuid),
        postgres=False,
    )
    return connection, job, fence, message.message_uuid
