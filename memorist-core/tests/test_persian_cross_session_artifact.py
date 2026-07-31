from __future__ import annotations

from pathlib import Path

import pytest

from memcore.config import Settings
from memcore.memory_worker.execution import ContractExecutionOutcome
from memcore.memory_worker.pipeline import MemoryWorkerPipeline
from memcore.models import utc_now
from memcore.preflight import PreflightRequest, PreflightService
from memcore.repositories import (
    MessageRepository,
    ProjectRepository,
    SessionRepository,
    WorkspaceRepository,
)
from memcore.storage.migrations import apply_migrations
from memcore.storage.sqlite import connect

pytestmark = pytest.mark.usefixtures("wp02_downstream_semantic_model")

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
    "تکنیک استفاده‌شده در مرحله بعد از مرحله ۶: مکانیسم‌های تأمین مالی لایه‌های عمیق را توضیح بده."
)


def test_multisentence_structural_semantic_unit_gets_exact_persisted_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = "Architecture decision approved. Architecture decision active."
    semantic_calls: list[bool] = []

    def semantic_contract(**_: object) -> ContractExecutionOutcome:
        semantic_calls.append(True)
        return ContractExecutionOutcome(
            output={
                "schema_version": "1.0",
                "prompt_id": "memorist.semantic_candidate_analysis",
                "prompt_version": "1.1",
                "status": "ok",
                "warnings": [],
                "semantic_units": [
                    {
                        "id": "multi-sentence-decision",
                        "raw_start": 0,
                        "raw_end": len(raw),
                        "evidence": raw,
                        "proposition": raw,
                        "unit_type": "fragment",
                        "memory_kind": "Decision",
                        "durability": "durable",
                        "polarity": "affirmed",
                        "epistemic_status": "asserted",
                    }
                ],
                "references": [],
                "relations": [],
            },
            status="succeeded",
            called_provider=True,
            provider_output_valid=True,
            canonicalized=False,
            repair_attempted=False,
            repair_succeeded=False,
            fallback_used=False,
            fallback_reason=None,
            capability_mode="test",
            provider_response_id="multi-span",
            input_tokens=10,
            output_tokens=10,
            latency_ms=1,
            parse_status="parsed",
            attempt_count=1,
            validation_error_paths=[],
        )

    monkeypatch.setattr(
        "memcore.memory_worker.semantic.orchestration.execute_semantic_candidate_contract",
        semantic_contract,
    )
    db_path = tmp_path / "multi-span.sqlite"
    connection = connect(db_path)
    apply_migrations(connection)
    settings = Settings(db_path=str(db_path), object_store_path=str(tmp_path / "objects"))
    workspace = WorkspaceRepository(connection).create_workspace("Workspace")
    project = ProjectRepository(connection).create_project(workspace.workspace_uuid, "Project")
    session = SessionRepository(connection).create_session(
        workspace_uuid=workspace.workspace_uuid,
        project_uuid=project.project_uuid,
    )
    connection.execute(
        "INSERT INTO memorist_session_actors "
        "(session_uuid, user_uuid, workspace_uuid, created_at) VALUES (?, ?, ?, ?)",
        (session.session_uuid, "user-1", workspace.workspace_uuid, utc_now()),
    )
    message = MessageRepository(connection).create_message(
        session.session_uuid,
        role="user",
        creator_type="user",
        raw_text=raw,
    )

    result = MemoryWorkerPipeline(connection, settings).process_message(message.message_uuid)
    assert semantic_calls == [True]

    fragment = connection.execute(
        "SELECT text_unit_uuid, text, start_char, end_char FROM text_units "
        "WHERE message_uuid = ? AND unit_type = 'fragment'",
        (message.message_uuid,),
    ).fetchone()
    assert fragment is not None, {
        "result": result,
        "text_units": [
            dict(row)
            for row in connection.execute(
                "SELECT unit_type, text, start_char, end_char FROM text_units "
                "WHERE message_uuid = ? ORDER BY unit_index",
                (message.message_uuid,),
            ).fetchall()
        ],
    }
    assert fragment["text"] == raw
    assert (fragment["start_char"], fragment["end_char"]) == (0, len(raw))
    evidence = connection.execute(
        "SELECT text_unit_uuid, evidence_text FROM candidate_evidence WHERE message_uuid = ?",
        (message.message_uuid,),
    ).fetchone()
    assert evidence is not None
    assert evidence["text_unit_uuid"] == fragment["text_unit_uuid"]
    assert evidence["evidence_text"] == raw
    assert result["candidates"] == 1
    connection.close()


@pytest.mark.parametrize("author_role", ["user", "assistant"])
def test_persian_eleven_stage_plan_legacy_gate_does_not_veto_semantic_analysis(
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

    processing = MemoryWorkerPipeline(connection, settings).process_message(source.message_uuid)
    assert preceding_user_uuid is None or author_role == "assistant"
    assert processing["semantic_terminal_gate_short_circuit"] is False
    assert processing["semantic_outcome"] == "succeeded_with_memory"
    assert processing["semantic_prompt_execution_uuid"] is not None
    candidate_count = processing["candidates"]
    assert isinstance(candidate_count, int)
    assert candidate_count > 0
    assert (
        connection.execute(
            "SELECT COUNT(*) FROM memory_candidates WHERE processing_run_uuid = ?",
            (processing["processing_run_uuid"],),
        ).fetchone()[0]
        == processing["candidates"]
    )
    dispositions = {
        row["disposition"]
        for row in connection.execute(
            """
            SELECT item.disposition
            FROM semantic_coverage_items item
            JOIN semantic_coverage_runs run
              ON run.coverage_run_uuid = item.coverage_run_uuid
            WHERE run.processing_run_uuid = ?
            """,
            (processing["processing_run_uuid"],),
        )
    }
    assert "durable_candidate" in dispositions
    # The single span that mentions private-data controls stays locally
    # review-bound, but it no longer suppresses semantic analysis for the rest
    # of the message.
    assert dispositions <= {"durable_candidate", "needs_review", "unsupported"}

    recall_session = sessions.create_session(
        workspace_uuid=workspace.workspace_uuid,
        project_uuid=project.project_uuid,
    )
    # Production always resolves a trusted actor before preflight; without one the
    # planning stage cannot attribute its plan and skips persistence entirely.
    for session_uuid in (source_session.session_uuid, recall_session.session_uuid):
        connection.execute(
            "INSERT INTO memorist_session_actors "
            "(session_uuid, user_uuid, workspace_uuid, created_at) VALUES (?, ?, ?, ?)",
            (session_uuid, "persian-user", workspace.workspace_uuid, utc_now()),
        )
    connection.commit()
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
            user_uuid="persian-user",
        )
    )

    assert response.rendered_attachment is not None
    assert response.attachment_uuid is not None
    assert response.retrieval_run_uuid is not None
    # The Lite preflight planning stage must record its execution and persist the
    # accepted plan under locally resolved scope. Both were silently lost while
    # the audit record was validated after secret redaction.
    planning = connection.execute(
        """
        SELECT status, error_sanitized FROM prompt_execution_runs
        WHERE message_uuid = ? AND prompt_id = 'memorist.preflight_planning'
        """,
        (query.message_uuid,),
    ).fetchone()
    assert planning is not None
    assert planning["status"] != "error", planning["error_sanitized"]
    plan = connection.execute(
        """
        SELECT user_uuid, workspace_uuid, project_uuid, input_message_uuid, requested_operation
        FROM model_retrieval_plans WHERE retrieval_run_uuid = ?
        """,
        (response.retrieval_run_uuid,),
    ).fetchone()
    assert plan is not None, "Lite preflight did not persist the accepted retrieval plan"
    assert plan["user_uuid"] == "persian-user"
    assert plan["workspace_uuid"] == workspace.workspace_uuid
    assert plan["project_uuid"] == project.project_uuid
    assert plan["input_message_uuid"] == query.message_uuid
    connection.close()
