from __future__ import annotations

import sqlite3
from collections.abc import Generator
from pathlib import Path

import pytest

from memcore.memory_worker.adapters.jakobson_to_candidate_input import (
    build_candidate_extraction_input,
    candidate_evidence_from_route,
)
from memcore.memory_worker.jakobson.service import JakobsonAnalysisService
from memcore.memory_worker.prompts.registry import (
    PromptValidationError,
    get_prompt,
    validate_prompt_output,
)
from memcore.memory_worker.prompts.schemas import valid_jakobson_output
from memcore.memory_worker.routing.signal_router import SignalRouter
from memcore.memory_worker.segmentation.sentence_segmenter import SentenceSegmenter
from memcore.models import (
    CandidateStatus,
    CandidateType,
    Explicitness,
    JakobsonConfidence,
    JakobsonFunction,
    JakobsonSentenceAnnotation,
    MemoryCandidate,
    MemorySignalRouteType,
    MessageRole,
    SourceAuthority,
    new_uuid,
)
from memcore.repositories import MessageRepository, SessionRepository
from memcore.repositories.memory_worker import (
    JakobsonAnalysisRepository,
    MemoryCandidateRepository,
    MemoryProcessingRunRepository,
    MemorySignalRouteRepository,
    TextUnitRepository,
    canonical_text_hash,
)
from memcore.storage.migrations import apply_migrations
from memcore.storage.sqlite import connect
from memcore.validators.ijson import dump_ijson

GOLDEN_PERSIAN_WORKFLOW = """سلام.
در پروژه Memorist، Jira منبع اصلی پیگیری کارها است.
Product Team باید هر تصمیم محصول را در Jira ثبت کند.
تیم توسعه باید قبل از شروع کار acceptance criteria را بخواند.
AI باید پرامپت کاربر را بدون تغییر نگه دارد.
برای اصطلاح Memory Signal Route، منظور مسیر هدایت استخراج حافظه است.
اگر جمله درباره کانفیگ Jira باشد، آن را به jira_configuration route بفرست.
اگر جمله دستور مستقیم به AI باشد، آن را prompt_instruction یا task_constraint حساب کن.
من ترجیح می‌دهم پاسخ‌ها دقیق و کوتاه باشند.
این فرایند برای auditability طراحی شده است.
لینک مرجع پروژه https://example.local/memorist است.
شعار تیم این است: حافظه دقیق، تصمیم دقیق.
"""


@pytest.fixture()
def connection(tmp_path: Path) -> Generator[sqlite3.Connection, None, None]:
    sqlite_connection = connect(tmp_path / "jakobson.sqlite")
    apply_migrations(sqlite_connection)
    try:
        yield sqlite_connection
    finally:
        sqlite_connection.close()


def test_sentence_segmentation_persian_workflow() -> None:
    result = SentenceSegmenter().segment(
        "11111111-1111-4111-8111-111111111111",
        GOLDEN_PERSIAN_WORKFLOW,
    )

    assert len(result.units) > 10
    assert [unit.sentence_index for unit in result.units] == list(range(1, len(result.units) + 1))
    assert any(unit.language_hint == "mixed" for unit in result.units)
    for unit in result.units:
        assert GOLDEN_PERSIAN_WORKFLOW[unit.char_start : unit.char_end] == unit.text
        assert unit.unit_uuid
        assert len(unit.content_hash) == 64
    assert [unit.model_dump() for unit in result.units] == [
        unit.model_dump()
        for unit in SentenceSegmenter().segment(
            "11111111-1111-4111-8111-111111111111",
            GOLDEN_PERSIAN_WORKFLOW,
        ).units
    ]


def test_sentence_segmentation_list_items() -> None:
    text = "۱. ابتدا issue را بساز.\n۲. سپس status را Done کن.\n3. Keep notes concise."
    units = SentenceSegmenter().segment("11111111-1111-4111-8111-111111111111", text).units

    assert len(units) == 3
    assert units[0].text.startswith("۱.")
    assert units[2].language_hint == "en"


def test_sentence_segmentation_english_paragraph() -> None:
    text = "Open WebUI remains the parent UI. Memorist stores local memory only."
    units = SentenceSegmenter().segment("11111111-1111-4111-8111-111111111111", text).units

    assert [unit.text for unit in units] == [
        "Open WebUI remains the parent UI.",
        " Memorist stores local memory only.",
    ]


def test_sentence_segmentation_mixed_persian_english() -> None:
    text = "Jira باید canonical tracker باشد. Use FastAPI for the API."
    units = SentenceSegmenter().segment("11111111-1111-4111-8111-111111111111", text).units

    assert len(units) == 2
    assert units[0].language_hint == "mixed"


def test_sentence_segmentation_headings_plus_content() -> None:
    text = "Policy:\nAI باید user prompt را تغییر ندهد."
    units = SentenceSegmenter().segment("11111111-1111-4111-8111-111111111111", text).units

    assert len(units) == 1
    assert units[0].text == text
    assert units[0].segmentation_notes == "heading joined with following sentence"


def test_sentence_segmentation_quoted_sentence() -> None:
    text = "مثال: «Product Team باید issue را ببندد.» این جمله فقط نمونه است."
    units = SentenceSegmenter().segment("11111111-1111-4111-8111-111111111111", text).units

    assert len(units) == 1
    assert "Product Team" in units[0].text


def test_sentence_segmentation_long_process_description() -> None:
    text = (
        "The workflow starts when a user creates a Jira ticket. "
        "The team reviews scope and records constraints. "
        "The AI only uses approved memory context."
    )
    units = SentenceSegmenter().segment("11111111-1111-4111-8111-111111111111", text).units

    assert len(units) == 3
    assert all(unit.char_end > unit.char_start for unit in units)


def test_sentence_segmentation_empty_whitespace_input() -> None:
    assert SentenceSegmenter().segment("11111111-1111-4111-8111-111111111111", " \n\t ").units == []


def test_sentence_segmentation_no_punctuation_input() -> None:
    units = SentenceSegmenter().segment(
        "11111111-1111-4111-8111-111111111111",
        "No punctuation here",
    ).units

    assert len(units) == 1
    assert units[0].segmentation_confidence == "low"


def test_jakobson_prompt_registry() -> None:
    prompt = get_prompt("memorist.jakobson_sentence_analysis")

    assert prompt.prompt_version == "2.0"
    assert prompt.stage == "jakobson_sentence_analysis"
    assert prompt.allowed_model_roles[0].value == "memory_extraction"
    assert "{{PAYLOAD_IJSON}}" in prompt.full_system_prompt


def test_jakobson_output_schema_valid() -> None:
    validate_prompt_output("memorist.jakobson_sentence_analysis", "2.0", valid_jakobson_output())


def test_jakobson_output_rejects_missing_factor() -> None:
    output = valid_jakobson_output()
    del output["sentences"][0]["six_factors"]["code"]

    with pytest.raises(PromptValidationError, match="missing six factor"):
        validate_prompt_output("memorist.jakobson_sentence_analysis", "2.0", output)


def test_jakobson_output_rejects_invalid_confidence() -> None:
    output = valid_jakobson_output()
    output["sentences"][0]["six_factors"]["code"]["confidence"] = "certain"

    with pytest.raises(PromptValidationError, match="confidence"):
        validate_prompt_output("memorist.jakobson_sentence_analysis", "2.0", output)


def test_jakobson_output_rejects_markdown_shape() -> None:
    with pytest.raises(PromptValidationError, match="missing required"):
        validate_prompt_output(
            "memorist.jakobson_sentence_analysis",
            "2.0",
            {"markdown": "```json"},
        )


def test_step1_migration_tables_and_columns_exist(connection: sqlite3.Connection) -> None:
    tables = {
        row["name"]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    text_unit_columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(text_units)").fetchall()
    }
    evidence_columns = {
        row["name"]
        for row in connection.execute("PRAGMA table_info(candidate_evidence)").fetchall()
    }

    assert {
        "jakobson_analysis_runs",
        "jakobson_sentence_annotations",
        "memory_signal_routes",
    } <= tables
    assert {
        "unit_uuid",
        "char_start",
        "char_end",
        "language_hint",
        "segmentation_confidence",
    } <= text_unit_columns
    assert {"annotation_uuid", "route_uuid"} <= evidence_columns


def test_jakobson_service_persists_run(connection: sqlite3.Connection) -> None:
    message = _message(connection, GOLDEN_PERSIAN_WORKFLOW)

    result = JakobsonAnalysisService(connection).run_for_message(message.message_uuid)
    runs = JakobsonAnalysisRepository(connection).list_runs_for_message(message.message_uuid)

    assert result["sentence_units"] > 10
    assert len(runs) == 1
    assert runs[0].prompt_id == "memorist.jakobson_sentence_analysis"
    assert runs[0].prompt_version == "2.0"
    assert runs[0].provider_type == "deterministic"
    assert runs[0].prompt_execution_uuid is not None


def test_jakobson_service_persists_sentence_annotations(connection: sqlite3.Connection) -> None:
    message = _message(connection, GOLDEN_PERSIAN_WORKFLOW)

    result = JakobsonAnalysisService(connection).run_for_message(message.message_uuid)
    annotations = JakobsonAnalysisRepository(connection).list_annotations_for_run(
        result["analysis_run_uuid"]
    )

    assert len(annotations) == result["sentence_units"]
    assert any(
        annotation.dominant_function is JakobsonFunction.CONATIVE
        for annotation in annotations
    )
    assert any(annotation.receiver_value == "Product Team" for annotation in annotations)
    assert any("Jira" in str(annotation.context_value) for annotation in annotations)


def test_jakobson_mapping_to_text_units(connection: sqlite3.Connection) -> None:
    message = _message(connection, "AI باید پرامپت کاربر را تغییر ندهد.")

    result = JakobsonAnalysisService(connection).run_for_message(message.message_uuid)
    units = TextUnitRepository(connection).list_units(message.message_uuid)
    annotations = JakobsonAnalysisRepository(connection).list_annotations_for_run(
        result["analysis_run_uuid"]
    )

    assert len(units) == len(annotations) == 1
    assert annotations[0].unit_uuid == units[0].text_unit_uuid
    assert annotations[0].sentence_text == units[0].text


def test_memory_signal_router_conative_ai() -> None:
    routes = SignalRouter().route(
        _annotation(
            "AI باید پرامپت را حفظ کند.",
            JakobsonFunction.CONATIVE,
            receiver="AI",
        )
    )

    assert routes[0].route_type in {
        MemorySignalRouteType.PROMPT_INSTRUCTION,
        MemorySignalRouteType.TASK_CONSTRAINT,
    }


def test_memory_signal_router_conative_team_policy() -> None:
    routes = SignalRouter().route(
        _annotation(
            "Product Team باید هر تصمیم را در Jira ثبت کند.",
            JakobsonFunction.CONATIVE,
            receiver="Product Team",
            context="Jira workflow",
        )
    )

    assert {route.route_type for route in routes} >= {
        MemorySignalRouteType.WORKFLOW_POLICY,
        MemorySignalRouteType.TEAM_OBLIGATION,
    }


def test_memory_signal_router_referential_process_fact() -> None:
    routes = SignalRouter().route(
        _annotation(
            "Jira منبع اصلی workflow پروژه است.",
            JakobsonFunction.REFERENTIAL,
            context="Jira workflow",
        )
    )

    assert routes[0].route_type is MemorySignalRouteType.JIRA_CONFIGURATION


def test_memory_signal_router_metalingual_prompt_rule() -> None:
    routes = SignalRouter().route(
        _annotation("منظور از prompt policy قانون نگارش پرامپت است.", JakobsonFunction.METALINGUAL)
    )

    assert routes[0].route_type in {
        MemorySignalRouteType.PROMPT_INSTRUCTION,
        MemorySignalRouteType.TERMINOLOGY_RULE,
    }


def test_candidate_lineage_includes_annotation_and_route(connection: sqlite3.Connection) -> None:
    message = _message(connection, "AI باید پرامپت کاربر را تغییر ندهد.")
    result = JakobsonAnalysisService(connection).run_for_message(message.message_uuid)
    annotation = JakobsonAnalysisRepository(connection).list_annotations_for_run(
        result["analysis_run_uuid"]
    )[0]
    route = MemorySignalRouteRepository(connection).list_for_annotation(
        annotation.annotation_uuid
    )[0]
    unit = TextUnitRepository(connection).get_unit(annotation.unit_uuid)
    assert unit is not None
    candidate_input = build_candidate_extraction_input(
        annotation=annotation,
        route=route,
        text_unit=unit,
        message=message,
    )

    run = MemoryProcessingRunRepository(connection).get_or_create_run(
        session_uuid=message.session_uuid,
        message_uuid=message.message_uuid,
        pipeline_version="test-jakobson-lineage",
        input_content_hash=canonical_text_hash(message.raw_text or ""),
    )
    candidate = MemoryCandidate(
        processing_run_uuid=run.processing_run_uuid,
        text_unit_uuid=unit.text_unit_uuid,
        candidate_type=CandidateType.INSTRUCTION,
        subject_key="ai",
        predicate="preserve_user_prompt",
        object_ijson=dump_ijson({"value": "do not mutate user prompt"}),
        normalized_text="instruction:ai:preserve_user_prompt",
        source_authority=SourceAuthority.USER_EXPLICIT,
        explicitness=Explicitness.EXPLICIT,
        confidence=0.9,
        importance=0.9,
        status=CandidateStatus.READY_FOR_CONSOLIDATION,
        extraction_metadata_ijson=dump_ijson(
            {
                "annotation_uuid": annotation.annotation_uuid,
                "route_uuid": route.route_uuid,
            }
        ),
    )
    evidence = candidate_evidence_from_route(
        candidate_uuid=candidate.candidate_uuid,
        message=message,
        text_unit=unit,
        annotation=annotation,
        route=route,
    )

    MemoryCandidateRepository(connection).create_candidate(candidate, [evidence])
    row = connection.execute(
        "SELECT annotation_uuid, route_uuid FROM candidate_evidence WHERE candidate_uuid = ?",
        (candidate.candidate_uuid,),
    ).fetchone()

    assert candidate_input["evidence"]["annotation_uuid"] == annotation.annotation_uuid
    assert row["annotation_uuid"] == annotation.annotation_uuid
    assert row["route_uuid"] == route.route_uuid


def test_legacy_unit_analysis_not_primary() -> None:
    jakobson = get_prompt("memorist.jakobson_sentence_analysis")
    legacy = get_prompt("memorist.unit_analysis")

    assert jakobson.stage == "jakobson_sentence_analysis"
    assert "primary" in jakobson.lifecycle.lower()
    assert "legacy" in legacy.lifecycle.lower()
    assert "legacy derived summary" in legacy.full_system_prompt


def _message(connection: sqlite3.Connection, text: str):
    session = SessionRepository(connection).create_session()
    return MessageRepository(connection).create_message(
        session.session_uuid,
        role=MessageRole.USER,
        creator_type="user",
        raw_text=text,
    )


def _annotation(
    text: str,
    dominant: JakobsonFunction,
    *,
    receiver: str | None = None,
    context: str | None = None,
) -> JakobsonSentenceAnnotation:
    return JakobsonSentenceAnnotation(
        analysis_run_uuid=new_uuid(),
        message_uuid=new_uuid(),
        unit_uuid=new_uuid(),
        sentence_index=1,
        sentence_text=text,
        sentence_hash=canonical_text_hash(text),
        sender_value="user",
        sender_evidence=text,
        sender_confidence=JakobsonConfidence.HIGH,
        receiver_value=receiver,
        receiver_evidence=receiver,
        receiver_confidence=JakobsonConfidence.MEDIUM,
        message_value=text,
        message_evidence=text,
        message_confidence=JakobsonConfidence.HIGH,
        context_value=context or text,
        context_evidence=context or text,
        context_confidence=JakobsonConfidence.MEDIUM,
        code_value="Persian; workflow terminology",
        code_evidence=text,
        code_confidence=JakobsonConfidence.HIGH,
        contact_channel_value="chat",
        contact_channel_evidence="chat",
        contact_channel_confidence=JakobsonConfidence.MEDIUM,
        dominant_function=dominant,
        secondary_functions_ijson=dump_ijson([]),
        function_reason="test",
        notes="",
        raw_sentence_output_ijson=dump_ijson({"text": text}),
    )
