from __future__ import annotations

from pathlib import Path

import pytest

from memcore.config import Settings
from memcore.memory_worker.pipeline import MemoryWorkerPipeline
from memcore.preflight import PreflightRequest, PreflightService
from memcore.repositories import (
    MessageRepository,
    ProjectRepository,
    SessionRepository,
    WorkspaceRepository,
)
from memcore.storage.migrations import apply_migrations
from memcore.storage.sqlite import connect
from memcore.validators.ijson import load_ijson

PERSIAN_ELEVEN_STAGE_PLAN = """طرح فنی یازده‌مرحله‌ای پروژه:
۱. تعریف دامنه، بازیگران و الزامات حقوقی.
۲. طراحی هویت دیجیتال و کنترل دسترسی اعضا.
۳. مدل‌سازی دارایی‌ها و تعهدات تجاری.
۴. ایجاد دفترکل مجاز و کانال‌های سازمانی.
۵. اتصال مؤسسات مالی و اعتبارسنجی تأمین‌کنندگان.
۶. تأمین مالی Deep-Tier و تقسیم توکن برای نقدشوندگی زنجیره تأمین.
۷. حریم خصوصی با Private Data Collections در Hyperledger Fabric برای محدودکردن داده
به اعضای مجاز، و Zero-Knowledge Proofs برای اثبات شرایط معامله بدون افشای داده محرمانه.
۸. پیام‌رسانی بین‌بانکی با ISO 20022 و SWIFT.
۹. تسویه اتمی دارایی و وجه و مدیریت نقدینگی.
۱۰. پایش ریسک، انطباق و گزارش‌دهی نظارتی.
۱۱. حاکمیت شبکه، رأی‌گیری و حل اختلاف."""

PERSIAN_FOLLOW_UP = (
    "تکنیک استفاده‌شده در مرحله بعد از مرحله ۶: مکانیسم‌های تأمین مالی "
    "لایه‌های عمیق را توضیح بده."
)


@pytest.mark.parametrize("author_role", ["user", "assistant"])
def test_persian_eleven_stage_plan_is_recalled_across_sessions_with_provenance(
    tmp_path: Path,
    author_role: str,
) -> None:
    db_path = tmp_path / f"persian-{author_role}.sqlite"
    connection = connect(db_path)
    apply_migrations(connection)
    settings = Settings(
        db_path=str(db_path),
        object_store_path=str(tmp_path / f"objects-{author_role}"),
    )
    workspace = WorkspaceRepository(connection).create_workspace("Persian workspace")
    project = ProjectRepository(connection).create_project(
        workspace.workspace_uuid,
        "Deep-tier project",
    )
    sessions = SessionRepository(connection)
    messages = MessageRepository(connection)
    source_session = sessions.create_session(
        workspace_uuid=workspace.workspace_uuid,
        project_uuid=project.project_uuid,
    )
    preceding_user_uuid: str | None = None
    if author_role == "assistant":
        preceding = messages.create_message(
            source_session.session_uuid,
            role="user",
            creator_type="user",
            raw_text="یک طرح فنی یازده‌مرحله‌ای برای این پروژه تهیه کن.",
        )
        preceding_user_uuid = preceding.message_uuid
    source = messages.create_message(
        source_session.session_uuid,
        role=author_role,
        creator_type="user" if author_role == "user" else "model",
        raw_text=PERSIAN_ELEVEN_STAGE_PLAN,
    )

    processing = MemoryWorkerPipeline(connection, settings).process_message(
        source.message_uuid
    )
    artifact = connection.execute(
        """
        SELECT *
        FROM memory_candidates
        WHERE processing_run_uuid = ? AND predicate = 'structured_project_artifact'
        """,
        (processing["processing_run_uuid"],),
    ).fetchone()
    assert artifact is not None
    assert artifact["status"] == "ready_for_consolidation"
    assert artifact["source_authority"] == (
        "assistant_claim" if author_role == "assistant" else "user_explicit"
    )
    metadata = load_ijson(artifact["extraction_metadata_ijson"])
    assert metadata["not_user_fact"] is (author_role == "assistant")
    assert metadata["preceding_user_message_uuid"] == preceding_user_uuid

    memory = connection.execute(
        """
        SELECT mv.normalized_text
        FROM memories m
        JOIN memory_versions mv ON mv.memory_version_uuid = m.current_version_uuid
        WHERE mv.source_candidate_uuid = ?
        """,
        (artifact["candidate_uuid"],),
    ).fetchone()
    assert memory is not None
    assert "۷." in memory["normalized_text"]
    assert "Private Data Collections" in memory["normalized_text"]
    assert "Zero-Knowledge Proofs" in memory["normalized_text"]

    recall_session = sessions.create_session(
        workspace_uuid=workspace.workspace_uuid,
        project_uuid=project.project_uuid,
    )
    query = messages.create_message(
        recall_session.session_uuid,
        role="user",
        creator_type="user",
        raw_text=PERSIAN_FOLLOW_UP,
    )
    response = PreflightService(connection, settings).run(
        PreflightRequest(
            session_uuid=recall_session.session_uuid,
            input_message_uuid=query.message_uuid,
            retrieval_mode="standard",
            token_budget=1400,
        )
    )

    assert response.rendered_attachment is not None
    assert "۷." in response.rendered_attachment
    assert "Private Data Collections" in response.rendered_attachment
    assert "Hyperledger Fabric" in response.rendered_attachment
    assert "Zero-Knowledge Proofs" in response.rendered_attachment
    if author_role == "assistant":
        assert "assistant_claim" in response.rendered_attachment
        assert "user_explicit" not in response.rendered_attachment
    connection.close()
