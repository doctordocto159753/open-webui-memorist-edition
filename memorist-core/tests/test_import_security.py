from __future__ import annotations

import logging
import stat
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

import pytest

from memcore.imports.runtime import import_connection, initialize_runtime_storage
from memcore.imports.security import ImportSecurityError, validate_zip_archive
from memcore.imports.service import ImportService
from memcore.imports.worker import ImportReconstructionWorkerService
from memcore.memory_worker.pipeline import MemoryWorkerPipeline
from memcore.model_control.security import sanitize_error_message
from test_import_worker_lifecycle import _archive, _commit, _settings

FAKE_PROVIDER_CREDENTIAL = "sk-" + "import-secret"

SECURITY_CORPUS = f"""
Authorization: Bearer {FAKE_PROVIDER_CREDENTIAL}
X-API-Key: another-secret
api_key=raw-import-key
token=raw-token
password=raw-password
https://user:pass@example.test/v1?token=query-secret
secret_env_var_name=VERY_PRIVATE_SECRET
""".strip()

RAW_SECRET_VALUES = {
    FAKE_PROVIDER_CREDENTIAL,
    "another-secret",
    "raw-import-key",
    "raw-token",
    "raw-password",
    "user:pass",
    "query-secret",
    "VERY_PRIVATE_SECRET",
}


def _assert_redacted(rendered: str) -> None:
    for secret in RAW_SECRET_VALUES:
        assert secret not in rendered


def test_import_error_security_corpus_is_redacted_everywhere(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings = _settings(tmp_path, concurrency=1)
    run_uuid = _commit(settings, tmp_path, monkeypatch, messages=1)

    def fail_with_secret(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise RuntimeError(SECURITY_CORPUS)

    monkeypatch.setattr(MemoryWorkerPipeline, "process_message", fail_with_secret)
    caplog.set_level(logging.DEBUG)
    worker = ImportReconstructionWorkerService(settings)
    assert worker.process_one_claimed_item(
        worker_id="security-test-worker",
        import_run_uuid=run_uuid,
    )

    with import_connection(settings) as connection:
        service = ImportService(connection, settings.object_store_path)
        stored = {
            "issues": [dict(row) for row in connection.execute("SELECT * FROM import_issues")],
            "statuses": service.message_processing_statuses(run_uuid),
            "jobs": [dict(row) for row in connection.execute("SELECT * FROM jobs")],
            "processing_runs": [
                dict(row) for row in connection.execute("SELECT * FROM memory_processing_runs")
            ],
            "jakobson_runs": [
                dict(row) for row in connection.execute("SELECT * FROM jakobson_analysis_runs")
            ],
            "prompt_executions": [
                dict(row) for row in connection.execute("SELECT * FROM prompt_execution_runs")
            ],
            "usage_events": [
                dict(row) for row in connection.execute("SELECT * FROM model_usage_events")
            ],
            "progress": service.progress(run_uuid),
            "processing_report": service.processing_report(run_uuid),
        }
    _assert_redacted(repr(stored))
    _assert_redacted(caplog.text)


def test_security_corpus_sanitizer_covers_headers_urls_and_env_references() -> None:
    sanitized = sanitize_error_message(SECURITY_CORPUS)
    assert sanitized is not None
    _assert_redacted(sanitized)
    assert "[redacted]" in sanitized.lower()


def test_commit_issue_persists_only_sanitized_error_corpus(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import memcore.imports.service as service_module

    settings = _settings(tmp_path, concurrency=1)
    monkeypatch.setattr(service_module, "get_settings", lambda: settings)
    initialize_runtime_storage(settings)
    with import_connection(settings) as connection:
        service = ImportService(connection, settings.object_store_path)
        run = service.upload(str(_archive(tmp_path, messages=1)))
        run_uuid = str(run["import_run_uuid"])
        service.inspect(run_uuid)
        service.reconstruct(run_uuid)
        service.dry_run(run_uuid, "full_memory_reconstruction")

        def fail_commit(*_args: object, **_kwargs: object) -> tuple[str, int]:
            raise RuntimeError(SECURITY_CORPUS)

        monkeypatch.setattr(ImportService, "_commit_conversation", fail_commit)
        service.commit(run_uuid, "full_memory_reconstruction")
        issues = [
            dict(row)
            for row in connection.execute(
                "SELECT * FROM import_issues WHERE import_run_uuid = ?", (run_uuid,)
            )
        ]
    assert issues
    _assert_redacted(repr(issues))


@pytest.mark.parametrize("member", ["../escape.json", "/absolute.json", "C:/absolute.json"])
def test_zip_traversal_and_absolute_paths_are_rejected(tmp_path: Path, member: str) -> None:
    archive = tmp_path / "unsafe.zip"
    with ZipFile(archive, "w") as zip_file:
        zip_file.writestr(member, "{}")
    with pytest.raises(ImportSecurityError):
        validate_zip_archive(str(archive))


def test_zip_symlink_and_device_entries_are_rejected(tmp_path: Path) -> None:
    for mode in (stat.S_IFLNK, stat.S_IFCHR):
        archive = tmp_path / f"unsafe-{mode}.zip"
        info = ZipInfo("unsafe-entry")
        info.create_system = 3
        info.external_attr = (mode | 0o777) << 16
        with ZipFile(archive, "w") as zip_file:
            zip_file.writestr(info, "target")
        with pytest.raises(ImportSecurityError):
            validate_zip_archive(str(archive))


def test_zip_compression_ratio_limit_is_enforced(tmp_path: Path) -> None:
    archive = tmp_path / "bomb.zip"
    with ZipFile(archive, "w", compression=ZIP_DEFLATED) as zip_file:
        zip_file.writestr("conversations.json", "0" * 1_000_000)
    with pytest.raises(ImportSecurityError, match="compression ratio"):
        validate_zip_archive(str(archive))
