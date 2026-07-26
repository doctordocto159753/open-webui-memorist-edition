import copy
import json
import sqlite3
import zipfile
from collections.abc import Generator
from pathlib import Path

import pytest

from memcore.config import Settings
from memcore.imports.processing import ImportMessageProcessor
from memcore.imports.security import ImportSecurityError
from memcore.imports.service import ImportService
from memcore.memory_worker.pipeline import MemoryWorkerPipeline
from memcore.memory_worker.prompts.schemas import valid_jakobson_output
from memcore.memory_worker.providers.base import ProviderResponse
from memcore.memory_worker.providers.openai_compatible import (
    OpenAICompatibleMemoryExtractionProvider,
)
from memcore.model_control.providers.base import ProviderHealth
from memcore.model_control.repository import ModelControlRepository
from memcore.model_control.role_contracts import role_contract_manifest_hash
from memcore.model_control.schemas import ModelProfileCreate, ProviderType
from memcore.models import ModelRole
from memcore.storage.migrations import apply_migrations
from memcore.storage.sqlite import connect
from memcore.validators.ijson import load_ijson


@pytest.fixture()
def connection(tmp_path: Path) -> Generator[sqlite3.Connection, None, None]:
    sqlite_connection = connect(tmp_path / "chatgpt-import.sqlite")
    apply_migrations(sqlite_connection)
    try:
        yield sqlite_connection
    finally:
        sqlite_connection.close()


def test_deterministic_import_fallback_requires_explicit_policy(
    connection: sqlite3.Connection, tmp_path: Path
) -> None:
    settings = Settings(
        db_path=str(tmp_path / "fallback.sqlite"),
        object_store_path=str(tmp_path / "objects"),
        import_reconstruction_allow_deterministic_fallback=False,
    )
    with pytest.raises(RuntimeError, match="deterministic fallback is disabled"):
        ImportMessageProcessor(connection, settings).model_target()


def test_official_zip_nested_and_standalone_json_use_same_adapter(
    connection: sqlite3.Connection, tmp_path: Path
) -> None:
    payload = _official_payload()
    standalone = tmp_path / "conversations.json"
    standalone.write_text(json.dumps(payload), encoding="utf-8")
    archive = tmp_path / "official-export.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("export/account/conversations.json", json.dumps(payload))
    root_archive = tmp_path / "official-export-root.zip"
    with zipfile.ZipFile(root_archive, "w") as output:
        output.writestr("conversations.json", json.dumps(payload))

    service = ImportService(connection, str(tmp_path / "objects"))
    standalone_run = service.upload(str(standalone))
    zip_run = service.upload(str(archive))
    root_zip_run = service.upload(str(root_archive))

    standalone_inspect = service.inspect(standalone_run["import_run_uuid"])
    zip_inspect = service.inspect(zip_run["import_run_uuid"])
    root_zip_inspect = service.inspect(root_zip_run["import_run_uuid"])

    assert standalone_inspect["source_platform"] == "chatgpt"
    assert zip_inspect["source_platform"] == "chatgpt"
    assert root_zip_inspect["source_platform"] == "chatgpt"
    assert standalone_inspect["detected_format"] == "chatgpt_conversation_mapping"
    assert zip_inspect["detected_format"] == "chatgpt_conversation_mapping"
    assert (
        service.repository.list_artifacts(standalone_run["import_run_uuid"])[0]["relative_path"]
        == "conversations.json"
    )


def test_malformed_standalone_json_and_absolute_zip_member_fail_safely(
    connection: sqlite3.Connection, tmp_path: Path
) -> None:
    malformed = tmp_path / "conversations.json"
    malformed.write_text("{not valid json", encoding="utf-8")
    service = ImportService(connection, str(tmp_path / "objects"))
    run = service.upload(str(malformed))
    inspected = service.inspect(run["import_run_uuid"])
    issues = service.repository.list_issues(run["import_run_uuid"])

    assert inspected["error_count"] == 1
    assert issues[0]["issue_code"] == "unrecognized_or_malformed_import_source"

    unsafe = tmp_path / "absolute.zip"
    with zipfile.ZipFile(unsafe, "w") as output:
        output.writestr("/absolute/conversations.json", "[]")
    with pytest.raises(ImportSecurityError, match="absolute path"):
        service.upload(str(unsafe))


def test_large_multi_conversation_official_fixture_reconstructs(
    connection: sqlite3.Connection, tmp_path: Path
) -> None:
    payload: list[dict[str, object]] = []
    for index in range(25):
        conversation = json.loads(json.dumps(_official_payload()[0]))
        conversation["id"] = f"conversation-{index}"
        conversation["title"] = f"Synthetic conversation {index}"
        payload.append(conversation)
    source = tmp_path / "large-conversations.json"
    source.write_text(json.dumps(payload), encoding="utf-8")
    service = ImportService(connection, str(tmp_path / "large-objects"))
    run = service.upload(str(source))
    service.inspect(run["import_run_uuid"])
    reconstructed = service.reconstruct(run["import_run_uuid"])
    dry_run = service.dry_run(run["import_run_uuid"], "full_memory_reconstruction")
    report = load_ijson(dry_run["report_ijson"])

    assert reconstructed["total_conversations"] == 25
    assert reconstructed["total_messages"] == 275
    assert report["eligible_processing_message_count"] == 75


def test_chatgpt_tree_metadata_and_reasoning_quarantine(
    connection: sqlite3.Connection, tmp_path: Path
) -> None:
    service, run_uuid = _reconstructed_service(connection, tmp_path)
    row = service.conversations(run_uuid)[0]
    conversation = load_ijson(row["normalized_conversation_ijson"])

    assert conversation["title"] == "Official synthetic export"
    assert conversation["active_leaf_id"] == "assistant-active"
    assert conversation["messages"]["user-1"]["branch_status"] == "active"
    assert conversation["messages"]["assistant-active"]["branch_status"] == "active"
    assert conversation["messages"]["assistant-branch"]["branch_status"] == "branch"
    assert conversation["messages"]["assistant-active"]["model_name"] == "gpt-4.1"
    assert (
        conversation["messages"]["assistant-active"]["metadata"]["attachments"][0]["name"]
        == "notes.txt"
    )
    reasoning = conversation["messages"]["reasoning-only"]["content_parts"][0]
    assert reasoning["part_type"] == "provider_internal_reasoning"
    assert reasoning["quarantined"] is True
    provider_parts = conversation["messages"]["reasoning-only"]["metadata"]["provider"]["chatgpt"][
        "message"
    ]["content"]["parts"]
    assert provider_parts == [{"quarantined": True}]


def test_dry_run_reports_eligibility_skips_model_and_jobs(
    connection: sqlite3.Connection, tmp_path: Path
) -> None:
    service, run_uuid = _reconstructed_service(connection, tmp_path)
    dry_run = service.dry_run(run_uuid, "full_memory_reconstruction")
    report = load_ijson(dry_run["report_ijson"])

    assert report["source_platform_display"] == "ChatGPT/OpenAI"
    assert report["conversation_count"] == 1
    assert report["message_count"] == 11
    assert report["eligible_processing_message_count"] == 3
    assert report["ineligible_processing_message_count"] == 8
    assert report["skip_reasons"] == {
        "empty_visible_text": 2,
        "null_source_message_node": 1,
        "provider_internal_reasoning_quarantined": 1,
        "unsupported_role_for_memory_processing": 4,
    }
    assert report["expected_memory_processing_jobs"] == 3
    assert report["processing_model"]["provider_type"] == "deterministic"
    assert report["deterministic_fallback"] is True
    assert "does not sample" not in report["large_reconstruction_warning"].lower()
    assert "no cost-based sampling" in report["large_reconstruction_warning"].lower()


def test_full_reconstruction_tracks_every_message_and_creates_memory_artifacts(
    connection: sqlite3.Connection, tmp_path: Path
) -> None:
    service, run_uuid = _reconstructed_service(connection, tmp_path)
    service.dry_run(run_uuid, "full_memory_reconstruction")

    committed = service.commit(run_uuid, "full_memory_reconstruction")
    statuses = service.message_processing_statuses(run_uuid)
    queued_jobs = connection.execute(
        "SELECT COUNT(*) FROM jobs WHERE job_type = 'memory_extraction'"
    ).fetchone()[0]

    assert committed["status"] == "processing"
    assert len(statuses) == 11
    assert sum(item["status"] == "queued" for item in statuses) == 3
    assert sum(item["status"] == "skipped" for item in statuses) == 8
    assert queued_jobs == 3
    assert all(item["provider_type"] == "deterministic" for item in statuses)

    paused = service.pause(run_uuid)
    assert paused["paused"] is True
    service.process_next_batch(run_uuid, batch_size=10)
    assert (
        sum(item["status"] == "queued" for item in service.message_processing_statuses(run_uuid))
        == 3
    )
    service.resume(run_uuid)

    report = service.process_next_batch(run_uuid, batch_size=10)
    final_statuses = service.message_processing_statuses(run_uuid)
    run = service.repository.get_run(run_uuid)

    assert run["status"] == "fully_reconstructed"
    assert report["processing_jobs_succeeded"] == 3
    assert report["processing_jobs_skipped"] == 8
    assert report["memory_candidates_created"] >= 1
    assert report["memory_versions_created"] >= 1
    assert report["prompt_execution_runs"] == 3
    assert report["model_usage_events"] >= 6
    assert all(item["status"] in {"succeeded", "skipped"} for item in final_statuses)
    succeeded = next(item for item in final_statuses if item["status"] == "succeeded")
    assert succeeded["memory_processing_run_uuid"]
    assert succeeded["prompt_execution_uuid"]
    prompt = connection.execute(
        "SELECT * FROM prompt_execution_runs WHERE prompt_execution_uuid = ?",
        (succeeded["prompt_execution_uuid"],),
    ).fetchone()
    assert prompt["import_run_uuid"] == run_uuid
    assert prompt["job_uuid"] == succeeded["job_uuid"]
    progress = service.progress(run_uuid)
    assert progress["messages_total"] == 11
    assert progress["messages_committed"] == 11
    assert progress["processing_jobs_succeeded"] == 3
    assert progress["processing_jobs_skipped"] == 8
    assert progress["finished_at"]

    counts_before = (
        connection.execute("SELECT COUNT(*) FROM memory_candidates").fetchone()[0],
        connection.execute("SELECT COUNT(*) FROM memory_versions").fetchone()[0],
    )
    service.process_next_batch(run_uuid, batch_size=10)
    counts_after = (
        connection.execute("SELECT COUNT(*) FROM memory_candidates").fetchone()[0],
        connection.execute("SELECT COUNT(*) FROM memory_versions").fetchone()[0],
    )
    assert counts_after == counts_before


def test_duplicate_reimport_records_already_processed_without_duplicate_messages(
    connection: sqlite3.Connection, tmp_path: Path
) -> None:
    first_service, first_run = _reconstructed_service(connection, tmp_path, "first.json")
    first_service.dry_run(first_run, "full_memory_reconstruction")
    first_service.commit(first_run, "full_memory_reconstruction")
    first_service.process_next_batch(first_run, 10)
    message_count = connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0]

    second_service, second_run = _reconstructed_service(connection, tmp_path, "second.json")
    second_service.dry_run(second_run, "full_memory_reconstruction")
    committed = second_service.commit(second_run, "full_memory_reconstruction")
    statuses = second_service.message_processing_statuses(second_run)

    assert committed["status"] == "fully_reconstructed"
    assert connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == message_count
    assert len(statuses) == 11
    assert {item["status"] for item in statuses} == {"already_processed", "skipped"}
    assert {item["skip_reason"] for item in statuses if item["status"] == "skipped"} == {
        "empty_visible_text",
        "null_source_message_node",
        "provider_internal_reasoning_quarantined",
        "unsupported_role_for_memory_processing",
    }
    assert all(
        item["memory_processing_run_uuid"]
        for item in statuses
        if item["status"] == "already_processed"
    )


def test_mapping_repair_dry_run_count_equals_newly_scheduled_jobs(
    connection: sqlite3.Connection, tmp_path: Path
) -> None:
    first_service, first_run = _reconstructed_service(connection, tmp_path, "repair-first.json")
    first_service.dry_run(first_run, "full_memory_reconstruction")
    first_service.commit(first_run, "full_memory_reconstruction")
    first_service.process_next_batch(first_run, 20)

    with connection:
        connection.execute(
            """
            DELETE FROM import_mappings
            WHERE source_platform = 'chatgpt'
              AND source_object_type = 'message'
              AND source_object_id = 'user-1'
            """
        )
    jobs_before = connection.execute(
        "SELECT COUNT(*) FROM jobs WHERE job_type = 'memory_extraction'"
    ).fetchone()[0]

    second_service, second_run = _reconstructed_service(connection, tmp_path, "repair-second.json")
    dry_run = second_service.dry_run(second_run, "full_memory_reconstruction")
    report = load_ijson(dry_run["report_ijson"])
    second_service.commit(second_run, "full_memory_reconstruction")
    jobs_after = connection.execute(
        "SELECT COUNT(*) FROM jobs WHERE job_type = 'memory_extraction'"
    ).fetchone()[0]

    assert report["mapping_repair_message_count"] == 1
    assert report["expected_memory_processing_jobs"] == jobs_after - jobs_before


def test_failed_processing_is_sanitized_and_retryable(
    connection: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, run_uuid = _reconstructed_service(connection, tmp_path)
    service.dry_run(run_uuid, "full_memory_reconstruction")
    service.commit(run_uuid, "full_memory_reconstruction")
    original = MemoryWorkerPipeline.prepare_message

    def fail_once(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise RuntimeError("provider failed with safe test detail")

    monkeypatch.setattr(MemoryWorkerPipeline, "prepare_message", fail_once)
    failed_report = service.process_next_batch(run_uuid, 1)
    failed = next(
        item for item in service.message_processing_statuses(run_uuid) if item["status"] == "failed"
    )

    assert failed_report["processing_jobs_failed"] == 1
    assert failed["error_sanitized"].startswith("RuntimeError:")
    retried = service.retry_failed_processing(run_uuid)
    assert retried["retried"] == 1
    monkeypatch.setattr(MemoryWorkerPipeline, "prepare_message", original)
    service.process_next_batch(run_uuid, 10)

    updated = service.message_processing_statuses(run_uuid)
    assert all(item["status"] in {"succeeded", "skipped"} for item in updated)
    assert next(item for item in updated if item["status"] == "succeeded")["retry_count"] == 1


def test_configured_memory_extraction_profile_is_recorded_for_import(
    connection: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_extract(
        _provider: OpenAICompatibleMemoryExtractionProvider,
        *,
        system_prompt: str,
        input_payload: dict[str, object],
    ) -> ProviderResponse:
        del system_prompt
        output = valid_jakobson_output()
        template = output["sentences"][0]
        source_sentences = input_payload["sentences"]
        assert isinstance(source_sentences, list)
        output["sentences"] = []
        for index, source in enumerate(source_sentences, start=1):
            assert isinstance(source, dict)
            sentence = copy.deepcopy(template)
            sentence["id"] = index
            sentence["text"] = str(source["text"])
            sentence["six_factors"]["message"]["value"] = str(source["text"])
            output["sentences"].append(sentence)
        output["sentence_count"] = len(output["sentences"])
        return ProviderResponse(output=output, input_tokens=12, output_tokens=8, latency_ms=3)

    monkeypatch.setattr(OpenAICompatibleMemoryExtractionProvider, "extract", fake_extract)
    monkeypatch.setenv("MEMORIST_IMPORT_TEST_KEY", "synthetic-test-secret")
    model_control = ModelControlRepository(connection)
    profile = model_control.create_profile(
        ModelProfileCreate(
            provider_type=ProviderType.OPENAI_COMPATIBLE_LLM,
            provider_name="synthetic-openai-compatible",
            model_name="synthetic-import-model",
            role=ModelRole.MEMORY_EXTRACTION,
            endpoint_url="https://models.example.test/v1",
            endpoint_is_local=False,
            secret_strategy="env_var",
            secret_env_var_name="MEMORIST_IMPORT_TEST_KEY",
            supports_json_mode=True,
            privacy_acknowledged=True,
        )
    )
    model_control.record_health_event(
        profile.model_profile_uuid,
        ProviderHealth(
            status="ok",
            provider_type=profile.provider_type,
            model_name=profile.model_name,
            latency_ms=1,
            local_only_safe=False,
            authentication_status="ok",
            role_compatibility_status="compatible",
            overall_status="ok",
            role_manifest_hash=role_contract_manifest_hash(profile.role),
            role_probe_status="compatible",
        ),
    )
    model_control.set_default(ModelRole.MEMORY_EXTRACTION, profile.model_profile_uuid)
    service, run_uuid = _reconstructed_service(connection, tmp_path)

    dry_run = service.dry_run(run_uuid, "full_memory_reconstruction")
    dry_run_report = load_ijson(dry_run["report_ijson"])
    service.commit(run_uuid, "full_memory_reconstruction")
    service.process_next_batch(run_uuid, 10)
    statuses = service.message_processing_statuses(run_uuid)
    prompt = connection.execute(
        """
        SELECT * FROM prompt_execution_runs
        WHERE import_run_uuid = ?
        ORDER BY created_at
        LIMIT 1
        """,
        (run_uuid,),
    ).fetchone()

    assert dry_run_report["processing_model"]["model_profile_uuid"] == profile.model_profile_uuid
    assert dry_run_report["processing_model"]["deterministic_fallback"] is False
    assert all(item["model_profile_uuid"] == profile.model_profile_uuid for item in statuses)
    assert prompt["provider_type"] == "openai_compatible_llm"
    assert prompt["model_name"] == "synthetic-import-model"


def _reconstructed_service(
    connection: sqlite3.Connection,
    tmp_path: Path,
    filename: str = "conversations.json",
) -> tuple[ImportService, str]:
    source = tmp_path / filename
    source.write_text(json.dumps(_official_payload()), encoding="utf-8")
    service = ImportService(connection, str(tmp_path / "objects"))
    run = service.upload(str(source))
    service.inspect(run["import_run_uuid"])
    service.reconstruct(run["import_run_uuid"])
    return service, str(run["import_run_uuid"])


def _official_payload() -> list[dict[str, object]]:
    def node(
        node_id: str,
        parent: str | None,
        children: list[str],
        role: str,
        parts: list[object],
        *,
        content_type: str = "text",
        metadata: dict[str, object] | None = None,
    ) -> dict[str, object]:
        return {
            "id": node_id,
            "parent": parent,
            "children": children,
            "message": {
                "id": node_id,
                "author": {"role": role},
                "create_time": 1_700_000_000.0,
                "content": {"content_type": content_type, "parts": parts},
                "metadata": metadata or {},
            },
        }

    return [
        {
            "id": "conversation-1",
            "title": "Official synthetic export",
            "create_time": 1_700_000_000.0,
            "update_time": 1_700_000_010.0,
            "current_node": "assistant-active",
            "default_model_slug": "gpt-4.1-mini",
            "mapping": {
                "root": {
                    "id": "root",
                    "parent": None,
                    "children": ["user-1"],
                    "message": None,
                },
                "user-1": node(
                    "user-1",
                    "root",
                    ["assistant-active", "assistant-branch"],
                    "user",
                    ["I prefer concise answers."],
                ),
                "assistant-active": node(
                    "assistant-active",
                    "user-1",
                    [
                        "system-1",
                        "tool-1",
                        "plugin-1",
                        "code-1",
                        "empty-1",
                        "missing-parts",
                        "reasoning-only",
                    ],
                    "assistant",
                    ["I will keep future answers concise."],
                    metadata={
                        "model_slug": "gpt-4.1",
                        "attachments": [{"name": "notes.txt", "size": 12}],
                    },
                ),
                "assistant-branch": node(
                    "assistant-branch",
                    "user-1",
                    [],
                    "assistant",
                    ["Alternate response."],
                ),
                "system-1": node("system-1", "assistant-active", [], "system", ["System metadata"]),
                "tool-1": node("tool-1", "assistant-active", [], "tool", ["Tool output"]),
                "plugin-1": node("plugin-1", "assistant-active", [], "plugin", ["Plugin output"]),
                "code-1": node("code-1", "assistant-active", [], "code", ["print('hello')"]),
                "empty-1": node("empty-1", "assistant-active", [], "user", [""]),
                "missing-parts": {
                    "id": "missing-parts",
                    "parent": "assistant-active",
                    "children": [],
                    "message": {
                        "id": "missing-parts",
                        "author": {"role": "assistant"},
                        "content": {"content_type": "text"},
                        "metadata": {"finish_details": {"type": "interrupted"}},
                    },
                },
                "reasoning-only": node(
                    "reasoning-only",
                    "assistant-active",
                    [],
                    "assistant",
                    ["private chain of thought"],
                    content_type="reasoning_recap",
                ),
            },
        }
    ]
