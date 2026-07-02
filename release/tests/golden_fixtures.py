from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from memcore.active_memory.repositories import ActiveMemoryRepository
from memcore.active_memory.schemas import BlockSource, BlockVersion
from memcore.governance.repositories import GovernanceRepository, hash_token
from memcore.imports.models import ImportRecord
from memcore.imports.repositories import ImportRepository
from memcore.models import (
    AttachmentMode,
    CandidateEvidence,
    CandidateType,
    ChangeOperation,
    ConsolidationOperation,
    CreatorType,
    EvidenceRole,
    Explicitness,
    Memory,
    MemoryBlockType,
    MemoryCandidate,
    MemoryVersion,
    MessageRole,
    SourceAuthority,
    SupportType,
    TextUnit,
    TextUnitType,
    utc_now,
)
from memcore.repositories import (
    MemoryBlockRepository,
    MemoryContextAttachmentRepository,
    MessageRepository,
    MessageVersionRepository,
    ProjectRepository,
    SessionHotCacheRepository,
    SessionRepository,
    WorkspaceRepository,
)
from memcore.repositories.memory_worker import (
    MemoryCandidateRepository,
    MemoryProcessingRunRepository,
    MemoryStoreRepository,
    TextUnitRepository,
    canonical_text_hash,
)
from memcore.retrieval.lexical import rebuild_index
from memcore.validators.ijson import canonical_hash_ijson, dump_ijson


def seed_rich_memory_fixture(
    connection: sqlite3.Connection,
    marker: str = "GOLDEN_MEMORY_MARKER",
    object_store_path: str | Path | None = None,
    include_receipt: bool = True,
) -> dict[str, Any]:
    workspace = WorkspaceRepository(connection).create_workspace("Golden Workspace")
    project = ProjectRepository(connection).create_project(
        workspace.workspace_uuid,
        "Golden Project",
    )
    session = SessionRepository(connection).create_session(
        workspace_uuid=workspace.workspace_uuid,
        project_uuid=project.project_uuid,
        title="Golden Roundtrip Session",
    )
    user_message = MessageRepository(connection).create_message(
        session.session_uuid,
        MessageRole.USER,
        CreatorType.USER,
        raw_text=f"Remember the project marker {marker}.",
        raw_payload={"marker": marker, "source": "golden-fixture"},
    )
    assistant_message = MessageRepository(connection).create_message(
        session.session_uuid,
        MessageRole.ASSISTANT,
        CreatorType.MODEL,
        raw_text=f"I will remember {marker} as untrusted memory data.",
        snapshot={"model": "golden-local-model", "marker": marker},
    )
    system_message = MessageRepository(connection).create_message(
        session.session_uuid,
        MessageRole.SYSTEM,
        CreatorType.SYSTEM,
        raw_text="System-like fixture message for Heritage roundtrip.",
    )
    tool_message = MessageRepository(connection).create_message(
        session.session_uuid,
        MessageRole.TOOL,
        CreatorType.TOOL,
        raw_text="Tool-like fixture observation.",
    )
    message_version = MessageVersionRepository(connection).create_version(
        assistant_message.message_uuid,
        raw_text=f"Versioned assistant text referencing {marker}.",
        change_reason="golden_fixture",
        created_by="test",
    )

    processing_run = MemoryProcessingRunRepository(connection).get_or_create_run(
        session.session_uuid,
        user_message.message_uuid,
        "golden-fixture-v1",
        canonical_text_hash(str(user_message.raw_text)),
    )
    processing_run = MemoryProcessingRunRepository(connection).mark_succeeded(
        processing_run.processing_run_uuid,
        raw_output={"fixture": "golden", "marker_hash": canonical_hash_ijson({"marker": marker})},
    )
    evidence_text = f"Remember the project marker {marker}."
    text_unit = TextUnit(
        message_uuid=user_message.message_uuid,
        session_uuid=session.session_uuid,
        unit_index=0,
        unit_type=TextUnitType.SENTENCE,
        text=evidence_text,
        start_char=0,
        end_char=len(evidence_text),
        language_code="en",
        speaker_role="user",
        content_hash=canonical_text_hash(evidence_text),
    )
    text_unit = TextUnitRepository(connection).create_units([text_unit])[0]
    candidate = MemoryCandidate(
        processing_run_uuid=processing_run.processing_run_uuid,
        text_unit_uuid=text_unit.text_unit_uuid,
        candidate_type=CandidateType.FACT,
        subject_key="project.marker",
        predicate="equals",
        object_ijson=dump_ijson({"marker": marker}),
        normalized_text=f"Project marker is {marker}.",
        source_authority=SourceAuthority.USER_EXPLICIT,
        explicitness=Explicitness.EXPLICIT,
        confidence=0.95,
        importance=0.75,
    )
    evidence = CandidateEvidence(
        candidate_uuid=candidate.candidate_uuid,
        message_uuid=user_message.message_uuid,
        text_unit_uuid=text_unit.text_unit_uuid,
        evidence_text=evidence_text,
        start_char=0,
        end_char=len(evidence_text),
        evidence_role=EvidenceRole.PRIMARY,
        support_type=SupportType.SUPPORTING,
    )
    candidate = MemoryCandidateRepository(connection).create_candidate(candidate, [evidence])
    stored_evidence = connection.execute(
        "SELECT * FROM candidate_evidence WHERE candidate_uuid = ?",
        (candidate.candidate_uuid,),
    ).fetchone()
    memory = Memory(
        canonical_key="project.marker",
        memory_type="fact",
        scope_type="project",
        scope_uuid=project.project_uuid,
    )
    memory_version = MemoryVersion(
        memory_uuid=memory.memory_uuid,
        version_number=1,
        normalized_text=f"Project marker is {marker}.",
        value_ijson=dump_ijson({"marker": marker, "trust": "untrusted_import_or_chat_data"}),
        confidence=0.95,
        importance=0.75,
        source_candidate_uuid=candidate.candidate_uuid,
        change_operation=ChangeOperation.ADD,
        change_reason_ijson=dump_ijson({"reason": "golden_fixture"}),
    )
    MemoryStoreRepository(connection).create_memory_with_version(
        memory,
        memory_version,
        candidate.candidate_uuid,
        ["golden_fixture"],
        ConsolidationOperation.ADD,
        [CandidateEvidence.model_validate(dict(stored_evidence))],
    )
    memory = MemoryStoreRepository(connection).get_memory(memory.memory_uuid)
    if memory is None:
        raise RuntimeError("golden memory was not created")

    block = MemoryBlockRepository(connection).create_block(
        MemoryBlockType.PROJECT_CONTEXT,
        "project",
        "Golden Project Context",
        f"Active block includes marker {marker}.",
        1_000,
        scope_uuid=project.project_uuid,
        source_memory_uuids=[memory.memory_uuid],
    )
    block_source = BlockSource(
        memory_uuid=memory.memory_uuid,
        memory_version_uuid=memory_version.memory_version_uuid,
        canonical_key=memory.canonical_key,
        memory_type=memory.memory_type,
        scope_type=memory.scope_type,
        scope_uuid=memory.scope_uuid,
        normalized_text=memory_version.normalized_text,
        source_authority="user_explicit",
        current=True,
        confidence=memory_version.confidence,
        sensitivity_class="normal",
    )
    block_version = BlockVersion(
        block_uuid=block.block_uuid,
        version_number=1,
        value=f"Golden block version preserves {marker}.",
        structured_value_ijson=dump_ijson(
            {"marker_hash": canonical_hash_ijson({"marker": marker})}
        ),
        char_count=len(marker),
        token_count=8,
        source_snapshot_hash=canonical_hash_ijson(
            {"memory_version": memory_version.memory_version_uuid}
        ),
        created_by="golden_fixture",
    )
    ActiveMemoryRepository(connection).create_version(
        block_version,
        [block_source],
        {memory_version.memory_version_uuid: "primary"},
    )
    ActiveMemoryRepository(connection).update_current_version(block, block_version)

    object_reference = _write_object_reference(object_store_path, marker)
    attachment = MemoryContextAttachmentRepository(connection).create_attachment(
        session_uuid=session.session_uuid,
        project_uuid=project.project_uuid,
        input_message_uuid=user_message.message_uuid,
        attachment_mode=AttachmentMode.STANDARD,
        ijson_attachment={
            "attachment_type": "golden_fixture",
            "marker": marker,
            "object_reference": object_reference,
            "untrusted": True,
        },
        rendered_attachment=f"Memorist context includes untrusted marker {marker}.",
        source_memory_uuids=[memory.memory_uuid],
        source_block_uuids=[block.block_uuid],
        token_count=24,
        confidence=0.9,
    )
    SessionHotCacheRepository(connection).upsert_cache(
        session.session_uuid,
        current_problem=f"Golden cache references {marker}.",
        active_constraints=[{"memory_uuid": memory.memory_uuid, "marker": marker}],
        recent_memory_uuids=[memory.memory_uuid],
    )

    import_run = ImportRepository(connection).create_run("1" * 64, "inspect", {"fixture": "golden"})
    ImportRepository(connection).update_run(
        import_run["import_run_uuid"],
        {"status": "committed", "source_platform": "openwebui"},
    )
    ImportRepository(connection).add_record(
        import_run["import_run_uuid"],
        ImportRecord(
            source_record_type="conversation",
            source_record_id="golden-conversation",
            raw_payload={"marker_hash": canonical_hash_ijson({"marker": marker})},
            normalized_payload={"source_platform": "openwebui", "marker": marker},
        ),
        0,
    )
    ImportRepository(connection).add_mapping(
        import_run["import_run_uuid"],
        "openwebui",
        "conversation",
        canonical_hash_ijson({"source": "golden-conversation"}),
        "session",
        session.session_uuid,
        source_object_id="golden-conversation",
    )

    receipt: dict[str, Any] | None = None
    if include_receipt:
        privacy_request_uuid = "11111111-1111-4111-8111-111111111111"
        GovernanceRepository(connection).create_privacy_request(
            request_uuid=privacy_request_uuid,
            request_type="forget",
            target_type="message",
            target_uuid=tool_message.message_uuid,
            requested_scope={"fixture": "golden", "target_hash": canonical_text_hash(marker)},
            actor_type="system",
            policy_decision={"decision": "golden_fixture_no_raw_content"},
            confirmation_token_hash=hash_token("golden-confirm"),
            confirmation_expires_at="2099-01-01T00:00:00Z",
            status="completed",
        )
        GovernanceRepository(connection).add_privacy_item(
            privacy_request_uuid,
            "sqlite",
            "message",
            "redact",
            tool_message.message_uuid,
            status="completed",
        )
        receipt = GovernanceRepository(connection).create_receipt(
            privacy_request_uuid,
            request_fingerprint=canonical_hash_ijson(
                {"target_uuid_hash": canonical_text_hash(tool_message.message_uuid)}
            ),
            erased_counts={"message": 1},
            retained_counts={"audit_tombstone": 1},
            exceptions=[],
            verification={"raw_erased_content_present": False},
        )
        GovernanceRepository(connection).update_privacy_request(
            privacy_request_uuid,
            {"completed_at": utc_now()},
        )
        with connection:
            connection.execute(
                """
                UPDATE messages
                SET raw_text = NULL,
                    raw_payload_ijson = NULL,
                    snapshot_ijson = NULL,
                    visibility = 'deleted',
                    is_deleted = 1,
                    redaction_status = 'erased',
                    updated_at = ?
                WHERE message_uuid = ?
                """,
                (utc_now(), tool_message.message_uuid),
            )

    rebuild_index(connection)
    return {
        "workspace_uuid": workspace.workspace_uuid,
        "project_uuid": project.project_uuid,
        "session_uuid": session.session_uuid,
        "user_message_uuid": user_message.message_uuid,
        "assistant_message_uuid": assistant_message.message_uuid,
        "system_message_uuid": system_message.message_uuid,
        "tool_message_uuid": tool_message.message_uuid,
        "message_version_uuid": message_version.message_version_uuid,
        "memory_uuid": memory.memory_uuid,
        "memory_version_uuid": memory_version.memory_version_uuid,
        "evidence_uuid": str(stored_evidence["evidence_uuid"]),
        "block_uuid": block.block_uuid,
        "block_version_uuid": block_version.block_version_uuid,
        "attachment_uuid": attachment.attachment_uuid,
        "import_run_uuid": import_run["import_run_uuid"],
        "erasure_receipt_uuid": receipt["erasure_receipt_uuid"] if receipt else None,
        "object_reference": object_reference,
        "marker": marker,
    }


def _write_object_reference(object_store_path: str | Path | None, marker: str) -> dict[str, Any]:
    if object_store_path is None:
        return {"status": "skipped_with_reason", "reason": "object_store_path_not_provided"}
    root = Path(object_store_path)
    root.mkdir(parents=True, exist_ok=True)
    object_path = root / "golden-object.txt"
    object_path.write_text(f"Object store fixture containing {marker}", encoding="utf-8")
    return {
        "status": "referenced",
        "relative_path": "golden-object.txt",
        "sha256": canonical_text_hash(object_path.read_text(encoding="utf-8")),
    }
