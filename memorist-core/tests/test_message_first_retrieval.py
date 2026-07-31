from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

from memcore.attachments.builder import AttachmentBuilder
from memcore.models import RetrievalMode, RetrievalRun, RetrievalRunStatus, utc_now
from memcore.repositories import (
    MessageRepository,
    ProjectRepository,
    SessionRepository,
    WorkspaceRepository,
)
from memcore.repositories.retrieval import RetrievalRepository
from memcore.retrieval.message_evidence import MessageEvidenceRetriever
from memcore.storage.migrations import apply_migrations
from memcore.storage.sqlite import connect
from memcore.validators.ijson import dump_ijson


def test_scf_stage_three_uses_alias_ordinal_and_message_evidence(tmp_path: Path) -> None:
    connection = connect(tmp_path / "scf.sqlite")
    apply_migrations(connection)
    workspace = WorkspaceRepository(connection).create_workspace("Workspace")
    project = ProjectRepository(connection).create_project(workspace.workspace_uuid, "SCF")
    sessions = SessionRepository(connection)
    source_session = sessions.create_session(
        workspace_uuid=workspace.workspace_uuid, project_uuid=project.project_uuid
    )
    recall_session = sessions.create_session(
        workspace_uuid=workspace.workspace_uuid, project_uuid=project.project_uuid
    )
    for session_uuid in (source_session.session_uuid, recall_session.session_uuid):
        connection.execute(
            "INSERT INTO memorist_session_actors "
            "(session_uuid, user_uuid, workspace_uuid, created_at) VALUES (?, ?, ?, ?)",
            (session_uuid, "user-1", workspace.workspace_uuid, utc_now()),
        )
    messages = MessageRepository(connection)
    source = messages.create_message(
        source_session.session_uuid,
        role="user",
        creator_type="user",
        raw_text="At stabilization phase C, isolate the supplier path before dispatch.",
    )
    query = messages.create_message(
        recall_session.session_uuid,
        role="user",
        creator_type="user",
        raw_text="در خصوص مرحله سوم SCF چه تدابیری باید اندیشید؟",
    )
    analysis_uuid = str(uuid.uuid4())
    processing_uuid = str(uuid.uuid4())
    now = utc_now()
    connection.execute(
        "INSERT INTO memory_processing_runs "
        "(processing_run_uuid, session_uuid, message_uuid, pipeline_version, "
        "input_content_hash, status, started_at, created_at) "
        "VALUES (?, ?, ?, 'test', ?, 'succeeded', ?, ?)",
        (
            processing_uuid,
            source_session.session_uuid,
            source.message_uuid,
            hashlib.sha256(str(source.raw_text).encode()).hexdigest(),
            now,
            now,
        ),
    )
    connection.execute(
        """
        INSERT INTO message_semantic_analyses (
          semantic_analysis_uuid, message_uuid, processing_run_uuid, workspace_uuid,
          project_uuid, session_uuid, user_uuid, source_role, source_authority,
          contract_hash, raw_text_hash, status, semantic_outcome, summary_intent,
          primary_topic, secondary_topic, one_line_summary, epistemic_status,
          temporal_status, importance, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'user', 'user_explicit', ?, ?, 'succeeded',
                  'succeeded_no_candidate', 'mitigation decision', 'supply continuity',
                  'stabilization phase C', ?, 'proposed', 'current', 0.9, ?, ?)
        """,
        (
            analysis_uuid,
            source.message_uuid,
            processing_uuid,
            workspace.workspace_uuid,
            project.project_uuid,
            source_session.session_uuid,
            "user-1",
            "contract",
            hashlib.sha256(str(source.raw_text).encode()).hexdigest(),
            "mitigation decision > supply continuity > stabilization phase C",
            now,
            now,
        ),
    )
    concept_uuid = str(uuid.uuid4())
    connection.execute(
        "INSERT INTO canonical_concepts (concept_uuid, canonical_label, created_at) "
        "VALUES (?, 'supply continuity framework', ?)",
        (concept_uuid, now),
    )
    connection.executemany(
        "INSERT INTO concept_aliases (concept_uuid, alias, normalized_alias) VALUES (?, ?, ?)",
        [(concept_uuid, "SCF", "scf"), (concept_uuid, "چارچوب تداوم تامین", "چارچوب تداوم تامین")],
    )
    connection.execute(
        "INSERT INTO message_concept_tags "
        "(semantic_analysis_uuid, concept_uuid, tag_ordinal, confidence) VALUES (?, ?, 0, 0.98)",
        (analysis_uuid, concept_uuid),
    )
    connection.execute(
        "INSERT INTO message_process_references "
        "(process_reference_uuid, semantic_analysis_uuid, process_label, "
        "process_aliases_ijson, stage_label, stage_ordinal, confidence) "
        "VALUES (?, ?, 'supply continuity framework', '[\"SCF\"]', 'phase C', 3, 0.99)",
        (str(uuid.uuid4()), analysis_uuid),
    )
    connection.commit()

    selected = MessageEvidenceRetriever(connection).retrieve(
        session_uuid=recall_session.session_uuid,
        input_message_uuid=query.message_uuid,
        query_understanding={
            "intent": "problem-solving/mitigation",
            "primary_topic": "scf",
            "secondary_topic": "risk controls",
            "entities": [],
            "process_label": "supply continuity framework",
            "stage_ordinal": 3,
        },
    )

    assert len(selected) == 1
    assert selected[0].memory_uuid == f"message:{source.message_uuid}"
    assert "supplier path" in str(selected[0].evidence_text)
    assert "مرحله سوم" not in str(source.raw_text)

    run = RetrievalRepository(connection).create_run(
        RetrievalRun(
            session_uuid=recall_session.session_uuid,
            project_uuid=project.project_uuid,
            input_message_uuid=query.message_uuid,
            retrieval_mode=RetrievalMode.STANDARD,
            original_query=str(query.raw_text),
            token_budget=1200,
            status=RetrievalRunStatus.COMPLETED,
            planner_version="test",
            config_snapshot_ijson=dump_ijson({}),
        )
    )
    attachment_uuid, rendered, _ = AttachmentBuilder(connection).build(
        run.retrieval_run_uuid,
        recall_session.session_uuid,
        query.message_uuid,
        "standard",
        selected,
        1200,
    )
    attachment = connection.execute(
        "SELECT * FROM memory_context_attachments WHERE attachment_uuid = ?",
        (attachment_uuid,),
    ).fetchone()
    assert attachment is not None
    assert "supplier path before dispatch" in rendered
    assert f"message:{source.message_uuid}" in rendered
    connection.close()


def test_message_evidence_never_crosses_user_boundary(tmp_path: Path) -> None:
    connection = connect(tmp_path / "scope.sqlite")
    apply_migrations(connection)
    workspace = WorkspaceRepository(connection).create_workspace("Workspace")
    project = ProjectRepository(connection).create_project(workspace.workspace_uuid, "Project")
    sessions = SessionRepository(connection)
    source_session = sessions.create_session(
        workspace_uuid=workspace.workspace_uuid, project_uuid=project.project_uuid
    )
    recall_session = sessions.create_session(
        workspace_uuid=workspace.workspace_uuid, project_uuid=project.project_uuid
    )
    now = utc_now()
    connection.executemany(
        "INSERT INTO memorist_session_actors "
        "(session_uuid, user_uuid, workspace_uuid, created_at) VALUES (?, ?, ?, ?)",
        [
            (source_session.session_uuid, "user-b", workspace.workspace_uuid, now),
            (recall_session.session_uuid, "user-a", workspace.workspace_uuid, now),
        ],
    )
    messages = MessageRepository(connection)
    source = messages.create_message(
        source_session.session_uuid,
        role="assistant",
        creator_type="model",
        raw_text="Private assistant project note.",
    )
    query = messages.create_message(
        recall_session.session_uuid,
        role="user",
        creator_type="user",
        raw_text="Recall the private project note.",
    )
    processing_uuid = str(uuid.uuid4())
    connection.execute(
        "INSERT INTO memory_processing_runs "
        "(processing_run_uuid, session_uuid, message_uuid, pipeline_version, "
        "input_content_hash, status, created_at) VALUES (?, ?, ?, 'test', ?, 'succeeded', ?)",
        (
            processing_uuid,
            source_session.session_uuid,
            source.message_uuid,
            source.content_hash,
            now,
        ),
    )
    connection.execute(
        """
        INSERT INTO message_semantic_analyses (
          semantic_analysis_uuid, message_uuid, processing_run_uuid, workspace_uuid,
          project_uuid, session_uuid, user_uuid, source_role, source_authority,
          contract_hash, raw_text_hash, status, semantic_outcome, primary_topic,
          one_line_summary, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, 'user-b', 'assistant', 'assistant_claim',
                  'contract', ?, 'succeeded', 'succeeded_no_candidate',
                  'private project note',
                  'assistant evidence > private project note > detail', ?, ?)
        """,
        (
            str(uuid.uuid4()),
            source.message_uuid,
            processing_uuid,
            workspace.workspace_uuid,
            project.project_uuid,
            source_session.session_uuid,
            source.content_hash,
            now,
            now,
        ),
    )
    connection.commit()

    selected = MessageEvidenceRetriever(connection).retrieve(
        session_uuid=recall_session.session_uuid,
        input_message_uuid=query.message_uuid,
        query_understanding={"primary_topic": "private project note", "entities": []},
    )

    assert selected == []
    connection.close()
