from __future__ import annotations

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, ClassVar
from uuid import uuid4
from zipfile import ZipFile

import pytest
from psycopg.errors import ForeignKeyViolation

from memcore.config import Settings
from memcore.imports.runtime import import_connection, initialize_runtime_storage
from memcore.imports.service import ImportService
from memcore.imports.worker import ImportReconstructionWorkerService
from memcore.memory_worker.prompts.schemas import valid_jakobson_output
from memcore.model_control.postgres_repository import PostgresModelControlRepository
from memcore.model_control.schemas import ModelProfileCreate, ProviderType
from memcore.models import ModelRole


class _ProviderHandler(BaseHTTPRequestHandler):
    requests: ClassVar[list[dict[str, Any]]] = []

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        input_payload = json.loads(payload["messages"][1]["content"])
        output = valid_jakobson_output()
        output["sentences"][0]["id"] = input_payload["sentences"][0]["id"]
        output["sentences"][0]["text"] = input_payload["sentences"][0]["text"]
        type(self).requests.append(
            {
                "path": self.path,
                "method": self.command,
                "authorization": self.headers.get("Authorization"),
                "model": payload.get("model"),
                "json": payload,
                "response_format": payload.get("response_format"),
            }
        )
        response = {
            "id": "fake-import-provider-response",
            "choices": [{"message": {"content": json.dumps(output)}}],
            "usage": {"prompt_tokens": 17, "completion_tokens": 11},
        }
        body = json.dumps(response).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        return


def _wait_for_terminal(settings: Settings, run_uuid: str, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with import_connection(settings) as connection:
            status = connection.execute(
                "SELECT status FROM import_runs WHERE import_run_uuid = ?", (run_uuid,)
            ).fetchone()["status"]
            if status in {"fully_reconstructed", "completed_with_failures"}:
                return
        time.sleep(0.05)
    raise AssertionError("remote-provider import did not complete before timeout")


@pytest.mark.skipif(not os.getenv("MEMORIST_POSTGRES_DSN"), reason="requires real PostgreSQL")
def test_full_postgres_remote_provider_background_worker_e2e(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ProviderHandler.requests = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ProviderHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    secret = "remote-provider-test-secret"
    monkeypatch.setenv("MEMORIST_REMOTE_PROVIDER_SECRET", secret)
    settings = Settings(
        runtime_profile="full",
        canonical_store="postgres",
        postgres_dsn=os.environ["MEMORIST_POSTGRES_DSN"],
        object_store_path=str(tmp_path / "objects"),
        db_path=str(tmp_path / "unused.sqlite3"),
        allow_full_graph_degraded=True,
        hot_scheduler="in_memory",
        import_reconstruction_worker_enabled=True,
        import_reconstruction_poll_seconds=1,
        import_reconstruction_lease_seconds=4,
        import_reconstruction_heartbeat_seconds=1,
    )
    import memcore.imports.service as service_module

    monkeypatch.setattr(service_module, "get_settings", lambda: settings)
    initialize_runtime_storage(settings)
    try:
        with import_connection(settings) as connection:
            connection.execute(
                "DELETE FROM model_role_defaults WHERE role = 'import_reconstruction'"
            )
            connection.commit()
            model_control = PostgresModelControlRepository(connection)
            profile = model_control.create_profile(
                ModelProfileCreate(
                    profile_name="remote import provider",
                    provider_type=ProviderType.OPENAI_COMPATIBLE_LLM,
                    provider_name="fake-openai-compatible",
                    model_name="fake-import-model",
                    role=ModelRole.IMPORT_RECONSTRUCTION,
                    endpoint_url=f"http://127.0.0.1:{server.server_port}/v1",
                    endpoint_is_local=False,
                    secret_strategy="env_var",
                    secret_env_var_name="MEMORIST_REMOTE_PROVIDER_SECRET",
                    supports_json_mode=True,
                    privacy_acknowledged=True,
                )
            )
            model_control.set_default(ModelRole.IMPORT_RECONSTRUCTION, profile.model_profile_uuid)
            service = ImportService(connection, settings.object_store_path)
            run = service.upload(str(_remote_archive(tmp_path, messages=2)))
            run_uuid = str(run["import_run_uuid"])
            service.inspect(run_uuid)
            service.reconstruct(run_uuid)
            dry_run = service.dry_run(run_uuid, "full_memory_reconstruction")
            assert json.loads(dry_run["report_ijson"])["expected_memory_processing_jobs"] == 2
            service.commit(run_uuid, "full_memory_reconstruction")

        worker = ImportReconstructionWorkerService(settings)
        worker.start()
        try:
            _wait_for_terminal(settings, run_uuid)
        finally:
            worker.stop()

        with import_connection(settings) as connection:
            service = ImportService(connection, settings.object_store_path)
            run = service.repository.get_run(run_uuid)
            statuses = service.message_processing_statuses(run_uuid)
            prompts = [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM prompt_execution_runs WHERE import_run_uuid = ?", (run_uuid,)
                )
            ]
            usage = [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM model_usage_events WHERE import_run_uuid = ?", (run_uuid,)
                )
            ]
            report = service.processing_report(run_uuid)
            database_rendering = repr(
                {
                    "statuses": statuses,
                    "prompts": prompts,
                    "usage": usage,
                    "report": report,
                }
            )

        assert run["status"] == "fully_reconstructed"
        assert _ProviderHandler.requests
        assert all(item["path"] == "/v1/chat/completions" for item in _ProviderHandler.requests)
        assert all(item["method"] == "POST" for item in _ProviderHandler.requests)
        assert all(item["model"] == "fake-import-model" for item in _ProviderHandler.requests)
        assert all(
            item["authorization"] == f"Bearer {secret}" for item in _ProviderHandler.requests
        )
        assert all(
            item["response_format"] == {"type": "json_object"} for item in _ProviderHandler.requests
        )
        eligible_statuses = [item for item in statuses if item["skip_reason"] is None]
        assert eligible_statuses
        assert all(
            item["status"] in {"succeeded", "already_processed"} for item in eligible_statuses
        )
        assert prompts and usage
        assert sum(int(item["input_tokens"]) for item in usage) >= 34
        assert sum(int(item["output_tokens"]) for item in usage) >= 22
        assert all(item["model_profile_uuid"] == profile.model_profile_uuid for item in prompts)
        assert all(item["provider_type"] == "openai_compatible_llm" for item in prompts)
        assert all(item["model_name"] == "fake-import-model" for item in prompts)
        assert all(item["model_role"] == "import_reconstruction" for item in prompts)
        assert all(item["import_run_uuid"] == run_uuid for item in prompts)
        assert all(item["job_uuid"] for item in prompts)
        assert secret not in database_rendering
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=2)


@pytest.mark.skipif(not os.getenv("MEMORIST_POSTGRES_DSN"), reason="requires real PostgreSQL")
def test_full_postgres_scheduled_profile_cannot_be_hard_deleted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ProviderHandler.requests = []
    settings = Settings(
        runtime_profile="full",
        canonical_store="postgres",
        postgres_dsn=os.environ["MEMORIST_POSTGRES_DSN"],
        object_store_path=str(tmp_path / "objects"),
        db_path=str(tmp_path / "unused.sqlite3"),
        allow_full_graph_degraded=True,
        hot_scheduler="in_memory",
        import_reconstruction_worker_enabled=True,
    )
    import memcore.imports.service as service_module

    monkeypatch.setattr(service_module, "get_settings", lambda: settings)
    initialize_runtime_storage(settings)
    with import_connection(settings) as connection:
        model_control = PostgresModelControlRepository(connection)
        profile = model_control.create_profile(
            ModelProfileCreate(
                profile_name="scheduled hard-delete guard",
                provider_type=ProviderType.OPENAI_COMPATIBLE_LLM,
                provider_name="fake-openai-compatible",
                model_name="fake-import-model",
                role=ModelRole.IMPORT_RECONSTRUCTION,
                endpoint_url="http://127.0.0.1:1/v1",
                endpoint_is_local=False,
                secret_strategy="env_var",
                secret_env_var_name="MEMORIST_IMPORT_TEST_KEY",
                supports_json_mode=True,
                privacy_acknowledged=True,
            )
        )
        model_control.set_default(ModelRole.IMPORT_RECONSTRUCTION, profile.model_profile_uuid)
        service = ImportService(connection, settings.object_store_path)
        run = service.upload(str(_remote_archive(tmp_path, messages=1)))
        run_uuid = str(run["import_run_uuid"])
        service.inspect(run_uuid)
        service.reconstruct(run_uuid)
        service.dry_run(run_uuid, "full_memory_reconstruction")
        service.commit(run_uuid, "full_memory_reconstruction")
        connection.commit()

        with pytest.raises(ForeignKeyViolation), connection.raw.transaction():
            connection.execute(
                "DELETE FROM model_role_defaults WHERE model_profile_uuid = ?",
                (profile.model_profile_uuid,),
            )
            connection.execute(
                "DELETE FROM model_profiles WHERE model_profile_uuid = ?",
                (profile.model_profile_uuid,),
            )
        persisted = connection.execute(
            "SELECT is_enabled FROM model_profiles WHERE model_profile_uuid = ?",
            (profile.model_profile_uuid,),
        ).fetchone()
        status = connection.execute(
            """
            SELECT model_profile_uuid, status
            FROM import_message_processing_status WHERE import_run_uuid = ?
            """,
            (run_uuid,),
        ).fetchone()
        ImportService(connection, settings.object_store_path).cancel(run_uuid)
    assert persisted is not None and persisted["is_enabled"]
    assert status["model_profile_uuid"] == profile.model_profile_uuid
    assert status["status"] == "queued"
    assert _ProviderHandler.requests == []


@pytest.mark.skipif(not os.getenv("MEMORIST_POSTGRES_DSN"), reason="requires real PostgreSQL")
@pytest.mark.parametrize(
    ("case_name", "mutation"),
    [
        ("profile_disabled_after_scheduling", "disable_profile"),
        ("profile_role_changed_after_scheduling", "change_role"),
        ("provider_type_unsupported", "unsupported_provider"),
        ("endpoint_missing", "missing_endpoint"),
        ("model_name_missing", "missing_model"),
        ("secret_env_var_name_missing", "missing_secret_name"),
        ("secret_env_var_unset", "unset_secret"),
        ("privacy_acknowledgement_missing", "missing_privacy_ack"),
        ("structured_output_capability_missing", "missing_structured_output"),
    ],
)
def test_full_postgres_remote_profile_failures_do_not_call_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case_name: str,
    mutation: str,
) -> None:
    _ProviderHandler.requests = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ProviderHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    secret = "negative-profile-secret"
    monkeypatch.setenv("MEMORIST_NEGATIVE_PROFILE_SECRET", secret)
    settings = Settings(
        runtime_profile="full",
        canonical_store="postgres",
        postgres_dsn=os.environ["MEMORIST_POSTGRES_DSN"],
        object_store_path=str(tmp_path / "objects"),
        db_path=str(tmp_path / "unused.sqlite3"),
        allow_full_graph_degraded=True,
        hot_scheduler="in_memory",
        import_reconstruction_worker_enabled=True,
        import_reconstruction_poll_seconds=1,
        import_reconstruction_lease_seconds=4,
        import_reconstruction_heartbeat_seconds=1,
    )
    import memcore.imports.service as service_module

    monkeypatch.setattr(service_module, "get_settings", lambda: settings)
    initialize_runtime_storage(settings)
    try:
        with import_connection(settings) as connection:
            model_control = PostgresModelControlRepository(connection)
            profile = model_control.create_profile(
                ModelProfileCreate(
                    profile_name=f"negative {case_name}",
                    provider_type=ProviderType.OPENAI_COMPATIBLE_LLM,
                    provider_name="fake-openai-compatible",
                    model_name="fake-import-model",
                    role=ModelRole.IMPORT_RECONSTRUCTION,
                    endpoint_url=f"http://127.0.0.1:{server.server_port}/v1",
                    endpoint_is_local=False,
                    secret_strategy="env_var",
                    secret_env_var_name="MEMORIST_NEGATIVE_PROFILE_SECRET",
                    supports_json_mode=True,
                    privacy_acknowledged=True,
                )
            )
            model_control.set_default(ModelRole.IMPORT_RECONSTRUCTION, profile.model_profile_uuid)
            service = ImportService(connection, settings.object_store_path)
            run = service.upload(str(_remote_archive(tmp_path, messages=1)))
            run_uuid = str(run["import_run_uuid"])
            service.inspect(run_uuid)
            service.reconstruct(run_uuid)
            service.dry_run(run_uuid, "full_memory_reconstruction")
            service.commit(run_uuid, "full_memory_reconstruction")
            if mutation == "disable_profile":
                connection.execute(
                    "UPDATE model_profiles SET is_enabled = false WHERE model_profile_uuid = ?",
                    (profile.model_profile_uuid,),
                )
            elif mutation == "change_role":
                connection.execute(
                    """
                    UPDATE model_profiles
                    SET role = 'memory_extraction'
                    WHERE model_profile_uuid = ?
                    """,
                    (profile.model_profile_uuid,),
                )
            elif mutation == "unsupported_provider":
                connection.execute(
                    """
                    UPDATE model_profiles
                    SET provider_type = 'unsupported'
                    WHERE model_profile_uuid = ?
                    """,
                    (profile.model_profile_uuid,),
                )
            elif mutation == "missing_endpoint":
                connection.execute(
                    "UPDATE model_profiles SET endpoint_url = NULL WHERE model_profile_uuid = ?",
                    (profile.model_profile_uuid,),
                )
            elif mutation == "missing_model":
                connection.execute(
                    "UPDATE model_profiles SET model_name = '   ' WHERE model_profile_uuid = ?",
                    (profile.model_profile_uuid,),
                )
            elif mutation == "missing_secret_name":
                connection.execute(
                    """
                    UPDATE model_profiles
                    SET secret_env_var_name = NULL
                    WHERE model_profile_uuid = ?
                    """,
                    (profile.model_profile_uuid,),
                )
            elif mutation == "unset_secret":
                monkeypatch.delenv("MEMORIST_NEGATIVE_PROFILE_SECRET", raising=False)
            elif mutation == "missing_privacy_ack":
                connection.execute(
                    """
                    UPDATE model_profiles
                    SET requires_privacy_acknowledgement = true,
                        privacy_acknowledged_at = NULL
                    WHERE model_profile_uuid = ?
                    """,
                    (profile.model_profile_uuid,),
                )
            elif mutation == "missing_structured_output":
                connection.execute(
                    """
                    UPDATE model_profiles
                    SET supports_json_mode = false,
                        supports_structured_output = false
                    WHERE model_profile_uuid = ?
                    """,
                    (profile.model_profile_uuid,),
                )
            connection.commit()

        report = ImportReconstructionWorkerService(settings).process_next_batch(
            import_run_uuid=run_uuid,
            limit=1,
        )

        with import_connection(settings) as connection:
            status = connection.execute(
                """
                SELECT status, error_classification, error_sanitized, run_after
                FROM import_message_processing_status
                WHERE import_run_uuid = ?
                """,
                (run_uuid,),
            ).fetchone()
            prompt_count = connection.execute(
                "SELECT COUNT(*) FROM prompt_execution_runs WHERE import_run_uuid = ?",
                (run_uuid,),
            ).fetchone()[0]
            usage_count = connection.execute(
                """
                SELECT COUNT(*) FROM model_usage_events
                WHERE import_run_uuid = ? AND stage = 'jakobson_sentence_analysis'
                """,
                (run_uuid,),
            ).fetchone()[0]
            database_rendering = repr(
                {
                    "status": dict(status),
                    "report": report,
                    "profile_uuid": profile.model_profile_uuid,
                }
            )

        assert _ProviderHandler.requests == []
        assert status["status"] == "failed"
        assert status["error_classification"] == "configuration_error"
        assert status["error_sanitized"]
        assert status["run_after"] is None
        assert prompt_count == 0
        assert usage_count == 0
        assert report["processing_jobs_failed"] == 1
        assert secret not in database_rendering
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=2)


def _remote_archive(path: Path, messages: int) -> Path:
    unique = uuid4().hex
    mapping: dict[str, object] = {
        "root": {"id": "root", "message": None, "parent": None, "children": ["m0"]}
    }
    for index in range(messages):
        node_id = f"m{index}"
        mapping[node_id] = {
            "id": node_id,
            "parent": "root" if index == 0 else f"m{index - 1}",
            "children": [f"m{index + 1}"] if index + 1 < messages else [],
            "message": {
                "id": f"{unique}-{node_id}",
                "author": {"role": "user" if index % 2 == 0 else "assistant"},
                "create_time": 1_700_000_000 + index,
                "content": {
                    "content_type": "text",
                    "parts": [f"Remote provider fact {unique} {index}."],
                },
            },
        }
    archive = path / f"remote-provider-{unique}.zip"
    with ZipFile(archive, "w") as zip_file:
        zip_file.writestr(
            "conversations.json",
            json.dumps(
                [
                    {
                        "id": f"remote-provider-{unique}",
                        "title": "Remote provider",
                        "create_time": 1_700_000_000,
                        "mapping": mapping,
                    }
                ]
            ),
        )
    return archive
