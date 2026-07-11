from __future__ import annotations

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, ClassVar

import pytest

from memcore.config import Settings
from memcore.imports.runtime import import_connection, initialize_runtime_storage
from memcore.imports.service import ImportService
from memcore.imports.worker import ImportReconstructionWorkerService
from memcore.memory_worker.prompts.schemas import valid_jakobson_output
from memcore.model_control.postgres_repository import PostgresModelControlRepository
from memcore.model_control.schemas import ModelProfileCreate, ProviderType
from memcore.models import ModelRole
from test_import_worker_lifecycle import _archive


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
            run = service.upload(str(_archive(tmp_path, messages=2)))
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
