from __future__ import annotations

import importlib
import os
import shutil
import sqlite3
from pathlib import Path
from statistics import median
from time import perf_counter
from typing import Any

import pytest

from memcore.memory_worker.postgres.semantic_coverage import (
    PostgresSemanticCoverageRepository,
)
from memcore.memory_worker.prompts.contracts import (
    SEMANTIC_CANDIDATE_V1_CONTRACT,
    SemanticAnalysisV1Output,
)
from memcore.memory_worker.semantic.candidate_mapping import (
    ROUTE_CANDIDATE_MAPPING_VERSION,
)
from memcore.memory_worker.semantic.coverage import (
    CoverageDisposition,
    CoveragePlannerInput,
    PersistedUnitAuthority,
    plan_candidate_coverage,
)
from memcore.memory_worker.semantic.provenance_policy import PROVENANCE_POLICY_VERSION
from memcore.memory_worker.semantic.runtime_adapters import (
    PostgresSemanticCandidateRuntimeAdapter,
    SQLiteSemanticCandidateRuntimeAdapter,
)
from memcore.memory_worker.semantic_contract import (
    BoundedContextItem,
    SemanticContextBoundary,
    build_semantic_input,
    validate_semantic_binding,
)
from memcore.memory_worker.semantic_coverage_persistence import (
    CoveragePersistenceBindings,
    SemanticCoverageIdentityConflict,
    candidate_payload_hash,
)
from memcore.reliability.consistency import run_consistency_check
from memcore.repositories.semantic_coverage import SQLiteSemanticCoverageRepository
from memcore.storage.migrations import apply_migrations
from memcore.storage.postgres.migrations import apply_postgres_migrations
from memcore.storage.postgres.parity import build_parity_report
from memcore.storage.sqlite import connect
from memcore.textsemantics import build_envelope, detect_context_dependency
from test_wp02_coverage_persistence import (
    _candidate,
    _cleanup_postgres,
    _plan,
    _seed_authority,
    _seed_postgres,
)


@pytest.mark.parametrize("omission", ["unit", "reference", "relation"])
def test_planner_rejects_incomplete_evidence_acceptance_sets(omission: str) -> None:
    value = _planner_case(2, with_closure=True)
    if omission == "unit":
        value = value.model_copy(update={"accepted_unit_ids": value.accepted_unit_ids[:-1]})
    elif omission == "reference":
        value = value.model_copy(
            update={"accepted_reference_indexes": value.accepted_reference_indexes[:-1]}
        )
    else:
        value = value.model_copy(
            update={"accepted_relation_indexes": value.accepted_relation_indexes[:-1]}
        )

    with pytest.raises(ValueError, match="must cover every semantic"):
        plan_candidate_coverage(value)


def test_deictic_hint_without_semantic_reference_fails_closed() -> None:
    raw = "It stays enabled."
    analysis = _analysis(
        raw,
        [
            _unit(
                unit_id="deictic",
                text=raw,
                raw_start=0,
                raw_end=len(raw),
            )
        ],
    )

    plan, proposals = plan_candidate_coverage(
        _planner_input(raw, analysis, (_authority(0, len(raw), 0),))
    )

    assert proposals == ()
    assert plan.items[0].disposition is CoverageDisposition.UNRESOLVED_REFERENCE
    assert "ambiguous_or_unresolved_reference" in plan.items[0].reason_codes


def test_each_dependency_hint_requires_a_reference_marker_covering_that_hint() -> None:
    raw = "It and that stay enabled."
    that_start = raw.index("that")
    analysis = _analysis(
        raw,
        [_unit(unit_id="deictic", text=raw, raw_start=0, raw_end=len(raw))],
        references=[
            {
                "id": "only-the-second-hint",
                "source_unit_id": "deictic",
                "marker_start": that_start,
                "marker_end": that_start + len("that"),
                "marker_evidence": "that",
                "status": "resolved",
                "candidate_referent_ids": ["prior_context:user-context"],
                "selected_referent_id": "prior_context:user-context",
            }
        ],
    )

    plan, proposals = plan_candidate_coverage(
        _planner_input(raw, analysis, (_authority(0, len(raw), 0),))
    )

    assert proposals == ()
    assert plan.items[0].disposition is CoverageDisposition.UNRESOLVED_REFERENCE


def test_resolved_reference_cannot_target_its_own_source_unit() -> None:
    raw = "It stays enabled."
    semantic_input = build_semantic_input(
        current_message_uuid="message-self-reference",
        current_message_version_uuid=None,
        current_raw_text=raw,
        text_envelope=build_envelope(raw),
        bounded_context_items=[],
        boundary=SemanticContextBoundary(
            user_uuid="user-a",
            session_uuid="session-a",
            workspace_uuid="workspace-a",
            project_uuid="project-a",
            baseline_limit=2,
            effective_limit=2,
            dependency_expansion=False,
        ),
    )
    output = _analysis(
        raw,
        [_unit(unit_id="self", text=raw, raw_start=0, raw_end=len(raw))],
        references=[
            {
                "id": "self-reference",
                "source_unit_id": "self",
                "marker_start": 0,
                "marker_end": 2,
                "marker_evidence": "It",
                "status": "resolved",
                "candidate_referent_ids": ["current_unit:self"],
                "selected_referent_id": "current_unit:self",
            }
        ],
    )

    with pytest.raises(ValueError, match="cannot target its own source unit"):
        validate_semantic_binding(
            semantic_input.model_dump(mode="json"),
            output.model_dump(mode="json"),
        )


def test_dependency_hint_lexicon_covers_english_persian_and_skips_code() -> None:
    raw = (
        "Use the previous and same setting؛ گزینه قبلی و بالا و پیشین.\n"
        "```text\nignore previous قبلی above بالا\n```"
    )

    hints = detect_context_dependency(raw)

    assert {hint.evidence.casefold() for hint in hints} >= {
        "previous",
        "same",
        "قبلی",
        "بالا",
        "پیشین",
    }
    fence_start = raw.index("```")
    assert all(hint.raw_end <= fence_start for hint in hints)
    assert all(raw[hint.raw_start : hint.raw_end] == hint.evidence for hint in hints)


def test_assistant_proposition_injection_requires_reference_and_ratification() -> None:
    raw = "Confirmed."
    assistant_text = "Production backups stay enabled."
    context = BoundedContextItem(
        context_item_id="assistant-context",
        user_uuid="user-a",
        session_uuid="session-a",
        workspace_uuid="workspace-a",
        project_uuid="project-a",
        message_uuid="assistant-message",
        message_version_uuid="assistant-version",
        text_unit_uuid="assistant-unit",
        role="assistant",
        turn_index=1,
        unit_index=0,
        raw_start=0,
        raw_end=len(assistant_text),
        text=assistant_text,
        raw_text_hash=_sha256(assistant_text),
        source_authority_ceiling="assistant_claim",
    )
    injected = _unit(unit_id="injected", text=raw, raw_start=0, raw_end=len(raw))
    injected["proposition"] = "Production backups are confirmed enabled."
    value = _planner_input(
        raw,
        _analysis(raw, [injected]),
        (_authority(0, len(raw), 0),),
    ).model_copy(update={"bounded_context_items": (context,)})

    plan, proposals = plan_candidate_coverage(value)

    assert proposals == ()
    assert plan.items[0].disposition is CoverageDisposition.NEEDS_REVIEW
    assert "conflicting_persisted_authority" in plan.items[0].reason_codes

    copied_raw = assistant_text
    copied = _planner_input(
        copied_raw,
        _analysis(
            copied_raw,
            [
                _unit(
                    unit_id="copied",
                    text=copied_raw,
                    raw_start=0,
                    raw_end=len(copied_raw),
                )
            ],
        ),
        (_authority(0, len(copied_raw), 0),),
    ).model_copy(update={"bounded_context_items": (context,)})

    copied_plan, copied_proposals = plan_candidate_coverage(copied)

    assert len(copied_proposals) == 1
    assert copied_plan.items[0].disposition is CoverageDisposition.DURABLE_CANDIDATE


@pytest.mark.parametrize("raw", ["Okay.", "متوجه شدم."])
def test_bare_acknowledgement_cannot_forge_assistant_ratification(raw: str) -> None:
    assistant_text = "Production backups stay enabled."
    context = BoundedContextItem(
        context_item_id="assistant-context",
        user_uuid="user-a",
        session_uuid="session-a",
        workspace_uuid="workspace-a",
        project_uuid="project-a",
        message_uuid="assistant-message",
        message_version_uuid="assistant-version",
        text_unit_uuid="assistant-unit",
        role="assistant",
        turn_index=1,
        unit_index=0,
        raw_start=0,
        raw_end=len(assistant_text),
        text=assistant_text,
        raw_text_hash=_sha256(assistant_text),
        source_authority_ceiling="assistant_claim",
    )
    unit = _unit(unit_id="acknowledgement", text=raw, raw_start=0, raw_end=len(raw))
    unit["proposition"] = assistant_text
    output = _analysis(
        raw,
        [unit],
        references=[
            {
                "id": "forged-assistant-reference",
                "source_unit_id": "acknowledgement",
                "marker_start": 0,
                "marker_end": len(raw),
                "marker_evidence": raw,
                "status": "resolved",
                "candidate_referent_ids": ["prior_context:assistant-context"],
                "selected_referent_id": "prior_context:assistant-context",
            }
        ],
        relations=[
            {
                "id": "forged-ratification",
                "relation_type": "ratifies",
                "source_unit_id": "acknowledgement",
                "target_referent_id": "prior_context:assistant-context",
                "evidence_start": 0,
                "evidence_end": len(raw),
                "evidence": raw,
            }
        ],
    )
    value = _planner_input(raw, output, (_authority(0, len(raw), 0),)).model_copy(
        update={"bounded_context_items": (context,)}
    )

    plan, proposals = plan_candidate_coverage(value)

    assert proposals == ()
    assert plan.items[0].disposition is CoverageDisposition.NEEDS_REVIEW
    assert "conflicting_persisted_authority" in plan.items[0].reason_codes


def test_explicit_persian_ratification_can_promote_one_assistant_claim() -> None:
    assistant_text = "پشتیبان‌گیری پروژه فعال می‌ماند."
    raw = f"بله، {assistant_text}"
    context = BoundedContextItem(
        context_item_id="assistant-context",
        user_uuid="user-a",
        session_uuid="session-a",
        workspace_uuid="workspace-a",
        project_uuid="project-a",
        message_uuid="assistant-message",
        message_version_uuid="assistant-version",
        text_unit_uuid="assistant-unit",
        role="assistant",
        turn_index=1,
        unit_index=0,
        raw_start=0,
        raw_end=len(assistant_text),
        text=assistant_text,
        raw_text_hash=_sha256(assistant_text),
        source_authority_ceiling="assistant_claim",
    )
    unit = _unit(unit_id="explicit-ratification", text=raw, raw_start=0, raw_end=len(raw))
    marker_start = raw.index(assistant_text)
    output = _analysis(
        raw,
        [unit],
        references=[
            {
                "id": "assistant-reference",
                "source_unit_id": "explicit-ratification",
                "marker_start": marker_start,
                "marker_end": len(raw),
                "marker_evidence": assistant_text,
                "status": "resolved",
                "candidate_referent_ids": ["prior_context:assistant-context"],
                "selected_referent_id": "prior_context:assistant-context",
            }
        ],
        relations=[
            {
                "id": "explicit-ratification-relation",
                "relation_type": "ratifies",
                "source_unit_id": "explicit-ratification",
                "target_referent_id": "prior_context:assistant-context",
                "evidence_start": 0,
                "evidence_end": len(raw),
                "evidence": raw,
            }
        ],
    )
    value = _planner_input(raw, output, (_authority(0, len(raw), 0),)).model_copy(
        update={"bounded_context_items": (context,)}
    )

    plan, proposals = plan_candidate_coverage(value)

    assert len(proposals) == 1
    assert plan.items[0].disposition is CoverageDisposition.DURABLE_CANDIDATE
    assert proposals[0].source_authority == "user_explicit"


def test_assistant_injection_guard_ignores_only_shared_function_words() -> None:
    raw = "The project is stable."
    assistant_text = "The weather is clear."
    context = BoundedContextItem(
        context_item_id="unrelated-assistant-context",
        user_uuid="user-a",
        session_uuid="session-a",
        workspace_uuid="workspace-a",
        project_uuid="project-a",
        message_uuid="assistant-message",
        message_version_uuid="assistant-version",
        text_unit_uuid="assistant-unit",
        role="assistant",
        turn_index=1,
        unit_index=0,
        raw_start=0,
        raw_end=len(assistant_text),
        text=assistant_text,
        raw_text_hash=_sha256(assistant_text),
        source_authority_ceiling="assistant_claim",
    )
    value = _planner_input(
        raw,
        _analysis(raw, [_unit(unit_id="project", text=raw, raw_start=0, raw_end=len(raw))]),
        (_authority(0, len(raw), 0),),
    ).model_copy(update={"bounded_context_items": (context,)})

    plan, proposals = plan_candidate_coverage(value)

    assert len(proposals) == 1
    assert plan.items[0].disposition is CoverageDisposition.DURABLE_CANDIDATE


def test_assistant_ratification_requires_one_candidate_referent() -> None:
    raw = "Yes, it stays enabled."
    current_unit = _unit(
        unit_id="ratification",
        text=raw,
        raw_start=0,
        raw_end=len(raw),
    )
    context_text = "Backups stay enabled."
    context = BoundedContextItem(
        context_item_id="assistant-context",
        user_uuid="user-a",
        session_uuid="session-a",
        workspace_uuid="workspace-a",
        project_uuid="project-a",
        message_uuid="assistant-message",
        message_version_uuid="assistant-version",
        text_unit_uuid="assistant-unit",
        role="assistant",
        turn_index=1,
        unit_index=0,
        raw_start=0,
        raw_end=len(context_text),
        text=context_text,
        raw_text_hash=_sha256(context_text),
        source_authority_ceiling="assistant_claim",
    )
    marker_start = raw.index("it")
    output = _analysis(
        raw,
        [current_unit],
        references=[
            {
                "id": "ambiguous-candidates",
                "source_unit_id": "ratification",
                "marker_start": marker_start,
                "marker_end": marker_start + 2,
                "marker_evidence": "it",
                "status": "resolved",
                "candidate_referent_ids": [
                    "prior_context:assistant-context",
                    "current_unit:ratification",
                ],
                "selected_referent_id": "prior_context:assistant-context",
            }
        ],
        relations=[
            {
                "id": "ratifies-assistant",
                "relation_type": "ratifies",
                "source_unit_id": "ratification",
                "target_referent_id": "prior_context:assistant-context",
                "evidence_start": 0,
                "evidence_end": len(raw),
                "evidence": raw,
            }
        ],
    )
    value = _planner_input(raw, output, (_authority(0, len(raw), 0),)).model_copy(
        update={"bounded_context_items": (context,)}
    )

    plan, proposals = plan_candidate_coverage(value)

    assert proposals == ()
    assert plan.items[0].disposition is CoverageDisposition.NEEDS_REVIEW
    assert "conflicting_persisted_authority" in plan.items[0].reason_codes

    malicious_payload = output.model_dump(mode="json")
    malicious_payload["references"][0]["candidate_referent_ids"] = [
        "prior_context:assistant-context"
    ]
    second_context = context.model_copy(
        update={
            "context_item_id": "assistant-context-2",
            "message_uuid": "assistant-message-2",
            "message_version_uuid": "assistant-version-2",
            "text_unit_uuid": "assistant-unit-2",
            "turn_index": 2,
        }
    )
    omitted_candidate_value = _planner_input(
        raw,
        SemanticAnalysisV1Output.model_validate(malicious_payload),
        (_authority(0, len(raw), 0),),
    ).model_copy(update={"bounded_context_items": (context, second_context)})

    omitted_plan, omitted_proposals = plan_candidate_coverage(omitted_candidate_value)

    assert omitted_proposals == ()
    assert omitted_plan.items[0].disposition is CoverageDisposition.NEEDS_REVIEW


def test_planner_scaling_regression_is_below_quadratic_baseline() -> None:
    """An 8x corpus increase stays below the measured pre-fix 20x+ slope."""

    small = _planner_case(128, with_closure=True)
    large = _planner_case(1024, with_closure=True)
    plan_candidate_coverage(small)
    plan_candidate_coverage(large)

    small_ms = _median_planner_ms(small, repeats=5)
    large_ms = _median_planner_ms(large, repeats=3)

    # Before the interval/index refactor this 8x vector measured above 20x.
    # A 16x allowance is deliberately loose for shared CI hosts while still
    # rejecting the former quadratic scan. The absolute bound only catches a
    # gross regression and is not a microbenchmark target.
    assert large_ms < max(2500.0, small_ms * 16.0)


def test_linked_sqlite_replay_rejects_candidate_tamper_and_diagnostics_find_it(
    tmp_path: Path,
) -> None:
    connection = connect(tmp_path / "operation7-replay.sqlite")
    apply_migrations(connection)
    try:
        _seed_authority(connection)
        plan, proposal, bindings = _plan()
        candidate, evidence = _candidate(proposal)
        repository = SQLiteSemanticCoverageRepository(connection)
        repository.persist_plan(plan, bindings)
        repository.reserve_candidate(
            proposal.proposal_id,
            plan.items[0].coverage_item_id,
            candidate_payload_hash(candidate, (evidence,)),
        )
        repository.create_and_link_candidate(proposal, candidate, (evidence,))
        connection.execute(
            "UPDATE memory_candidates SET predicate = 'tampered' WHERE candidate_uuid = ?",
            (proposal.proposal_id,),
        )
        connection.commit()

        with pytest.raises(
            SemanticCoverageIdentityConflict,
            match="existing candidate differs at predicate",
        ):
            repository.create_and_link_candidate(proposal, candidate, (evidence,))

        report = run_consistency_check(connection)
        assert report["status"] == "issues_found"
        assert {(issue["check"], issue["id"]) for issue in report["issues"]} >= {
            ("semantic_candidate_payload_hash", proposal.proposal_id)
        }
    finally:
        connection.close()


def test_sqlite_cross_run_replay_reuses_linked_candidate_and_same_text_version_fails(
    tmp_path: Path,
) -> None:
    connection = connect(tmp_path / "operation7-cross-run.sqlite")
    apply_migrations(connection)
    try:
        _seed_authority(connection)
        plan, proposal, bindings = _plan()
        version_uuid = "00000000-0000-4000-8000-000000000097"
        connection.execute(
            """
            INSERT INTO message_versions (
              message_version_uuid, message_uuid, version_number, raw_text,
              created_by, created_at
            ) VALUES (?, ?, 1, ?, 'operation7', '2026-01-01T00:00:00Z')
            """,
            (version_uuid, plan.message_uuid, "Keep backups enabled."),
        )
        connection.commit()
        bindings = CoveragePersistenceBindings(
            message_version_uuid=version_uuid,
            text_envelope_contract_version=bindings.text_envelope_contract_version,
            semantic_unit_fingerprints=bindings.semantic_unit_fingerprints,
            annotation_uuids=bindings.annotation_uuids,
        )
        candidate, evidence = _candidate(proposal)
        repository = SQLiteSemanticCoverageRepository(connection)
        repository.persist_plan(plan, bindings)
        repository.reserve_candidate(
            proposal.proposal_id,
            plan.items[0].coverage_item_id,
            candidate_payload_hash(candidate, (evidence,)),
        )
        repository.create_and_link_candidate(proposal, candidate, (evidence,))
        current_authority = _authority(0, len(evidence.evidence_text), 0).model_copy(
            update={
                "raw_end": len(evidence.evidence_text),
                "text_unit_uuid": proposal.text_unit_uuid,
                "annotation_uuid": proposal.annotation_uuid,
                "gate_decision_uuid": proposal.gate_decision_uuid,
                "route_uuid": proposal.route_uuid,
            }
        )
        adapter = SQLiteSemanticCandidateRuntimeAdapter(connection)

        replay = adapter.load_completed_semantic_planning(
            message_uuid=plan.message_uuid,
            processing_run_uuid="00000000-0000-4000-8000-000000000099",
            message_version_uuid=version_uuid,
            raw_text_hash=plan.raw_text_hash,
            semantic_contract_hash=plan.semantic_contract_hash,
            route_mapping_version=bindings.route_mapping_version,
            provenance_policy_version=bindings.provenance_policy_version,
            privacy_policy_version=bindings.privacy_policy_version,
            current_authorities=(current_authority,),
        )

        assert replay is not None
        assert replay.candidate_uuids == (proposal.proposal_id,)
        assert connection.execute("SELECT COUNT(*) FROM memory_candidates").fetchone()[0] == 1
        assert (
            connection.execute("SELECT COUNT(*) FROM semantic_candidate_links").fetchone()[0] == 1
        )

        with pytest.raises(RuntimeError, match="policy version changed"):
            adapter.load_completed_semantic_planning(
                message_uuid=plan.message_uuid,
                processing_run_uuid=plan.processing_run_uuid,
                message_version_uuid=version_uuid,
                raw_text_hash=plan.raw_text_hash,
                semantic_contract_hash=plan.semantic_contract_hash,
                route_mapping_version="operation7-route-policy-v2",
                provenance_policy_version=bindings.provenance_policy_version,
                privacy_policy_version=bindings.privacy_policy_version,
                current_authorities=(current_authority,),
            )

        with pytest.raises(RuntimeError, match="same-text message version"):
            adapter.load_completed_semantic_planning(
                message_uuid=plan.message_uuid,
                processing_run_uuid="00000000-0000-4000-8000-000000000099",
                message_version_uuid="00000000-0000-4000-8000-000000000098",
                raw_text_hash=plan.raw_text_hash,
                semantic_contract_hash=plan.semantic_contract_hash,
                route_mapping_version=bindings.route_mapping_version,
                provenance_policy_version=bindings.provenance_policy_version,
                privacy_policy_version=bindings.privacy_policy_version,
                current_authorities=(current_authority,),
            )
    finally:
        connection.close()


@pytest.mark.skipif(not os.getenv("MEMORIST_POSTGRES_DSN"), reason="requires real PostgreSQL")
def test_postgres_replay_checks_version_binding_and_linked_candidate_content() -> None:
    psycopg = importlib.import_module("psycopg")
    dict_row = importlib.import_module("psycopg.rows").dict_row
    connection = psycopg.connect(os.environ["MEMORIST_POSTGRES_DSN"])
    plan, proposal, bindings = _plan()
    candidate, evidence = _candidate(proposal)
    first_version = "00000000-0000-4000-8000-000000000010"
    second_version = "00000000-0000-4000-8000-000000000011"
    try:
        apply_postgres_migrations(connection)
        connection.row_factory = dict_row
        _cleanup_postgres(connection, proposal.proposal_id)
        _seed_postgres(connection)
        for version_number, version_uuid in enumerate(
            (first_version, second_version),
            start=1,
        ):
            connection.execute(
                """
                INSERT INTO message_versions (
                  message_version_uuid, message_uuid, version_number,
                  raw_text, created_by
                ) VALUES (%s, %s, %s, %s, 'operation7')
                ON CONFLICT DO NOTHING
                """,
                (
                    version_uuid,
                    plan.message_uuid,
                    version_number,
                    "Keep backups enabled.",
                ),
            )
        connection.commit()
        first_bindings = CoveragePersistenceBindings(
            message_version_uuid=first_version,
            text_envelope_contract_version=bindings.text_envelope_contract_version,
            semantic_unit_fingerprints=bindings.semantic_unit_fingerprints,
            annotation_uuids=bindings.annotation_uuids,
        )
        repository = PostgresSemanticCoverageRepository(connection)
        repository.persist_plan(plan, first_bindings)
        changed_bindings = CoveragePersistenceBindings(
            message_version_uuid=second_version,
            text_envelope_contract_version=bindings.text_envelope_contract_version,
            semantic_unit_fingerprints=bindings.semantic_unit_fingerprints,
            annotation_uuids=bindings.annotation_uuids,
        )
        with pytest.raises(
            SemanticCoverageIdentityConflict,
            match="deterministic coverage replay differs",
        ):
            repository.persist_plan(plan, changed_bindings)

        repository.reserve_candidate(
            proposal.proposal_id,
            plan.items[0].coverage_item_id,
            candidate_payload_hash(candidate, (evidence,)),
        )
        repository.create_and_link_candidate(proposal, candidate, (evidence,))
        runtime = PostgresSemanticCandidateRuntimeAdapter(connection)
        current_authority = _authority(0, len(evidence.evidence_text), 0).model_copy(
            update={
                "text_unit_uuid": proposal.text_unit_uuid,
                "annotation_uuid": proposal.annotation_uuid,
                "gate_decision_uuid": proposal.gate_decision_uuid,
                "route_uuid": proposal.route_uuid,
            }
        )
        replay = runtime.load_completed_semantic_planning(
            message_uuid=plan.message_uuid,
            processing_run_uuid=plan.processing_run_uuid,
            message_version_uuid=first_version,
            raw_text_hash=plan.raw_text_hash,
            semantic_contract_hash=plan.semantic_contract_hash,
            route_mapping_version=first_bindings.route_mapping_version,
            provenance_policy_version=first_bindings.provenance_policy_version,
            privacy_policy_version=first_bindings.privacy_policy_version,
            current_authorities=(current_authority,),
        )
        assert replay is not None
        assert replay.candidate_uuids == (proposal.proposal_id,)
        with pytest.raises(RuntimeError, match="policy version changed"):
            runtime.load_completed_semantic_planning(
                message_uuid=plan.message_uuid,
                processing_run_uuid=plan.processing_run_uuid,
                message_version_uuid=first_version,
                raw_text_hash=plan.raw_text_hash,
                semantic_contract_hash=plan.semantic_contract_hash,
                route_mapping_version="operation7-route-policy-v2",
                provenance_policy_version=first_bindings.provenance_policy_version,
                privacy_policy_version=first_bindings.privacy_policy_version,
                current_authorities=(current_authority,),
            )
        connection.execute(
            "UPDATE memory_candidates SET predicate = 'tampered' WHERE candidate_uuid = %s",
            (proposal.proposal_id,),
        )
        connection.commit()
        with pytest.raises(
            SemanticCoverageIdentityConflict,
            match="existing candidate differs at predicate",
        ):
            repository.create_and_link_candidate(proposal, candidate, (evidence,))
    finally:
        connection.rollback()
        try:
            connection.execute(
                "DELETE FROM message_versions WHERE message_version_uuid IN (%s, %s)",
                (first_version, second_version),
            )
            connection.commit()
            _cleanup_postgres(connection, proposal.proposal_id)
        except Exception:
            connection.rollback()
        connection.close()


def test_sqlite_migration_and_record_are_atomic_after_script_failure(
    tmp_path: Path,
) -> None:
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    migration = migrations / "0001_interrupted.sql"
    migration.write_text(
        "CREATE TABLE operation7_partial(id TEXT PRIMARY KEY);\nTHIS IS NOT VALID SQL;\n",
        encoding="utf-8",
    )
    connection = connect(tmp_path / "atomic.sqlite")
    try:
        with pytest.raises(sqlite3.OperationalError):
            apply_migrations(connection, migrations)
        assert (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE name = 'operation7_partial'"
            ).fetchone()
            is None
        )
        assert connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 0

        migration.write_text(
            "CREATE TABLE operation7_partial(id TEXT PRIMARY KEY);\n",
            encoding="utf-8",
        )
        apply_migrations(connection, migrations)
        assert (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE name = 'operation7_partial'"
            ).fetchone()
            is not None
        )
        assert connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 1
    finally:
        connection.close()


def test_schema_parity_fails_for_missing_table_or_weakened_wp02_constraint(
    tmp_path: Path,
) -> None:
    source_root = Path(__file__).resolve().parents[1]
    sqlite_migrations = tmp_path / "sqlite"
    postgres_migrations = tmp_path / "postgres"
    shutil.copytree(source_root / "migrations", sqlite_migrations)
    shutil.copytree(
        source_root / "src" / "memcore" / "storage" / "postgres" / "migrations",
        postgres_migrations,
    )

    missing_table = sqlite_migrations / "0037_semantic_coverage_audit.sql"
    missing_table.write_text(
        missing_table.read_text(encoding="utf-8").replace(
            "CREATE TABLE semantic_candidate_links (",
            "CREATE TABLE operation7_removed_semantic_candidate_links (",
            1,
        ),
        encoding="utf-8",
    )
    missing_report = build_parity_report(sqlite_migrations, postgres_migrations)
    assert missing_report["status"] == "fail"
    assert "semantic_candidate_links" in missing_report["missing_in_sqlite"]

    shutil.rmtree(sqlite_migrations)
    shutil.copytree(source_root / "migrations", sqlite_migrations)
    postgres_wp02 = postgres_migrations / "0024_semantic_coverage_audit.sql"
    postgres_wp02.write_text(
        postgres_wp02.read_text(encoding="utf-8").replace(
            "CHECK(support_type IN ('supporting', 'contradicting'))",
            "CHECK(support_type <> '')",
            1,
        ),
        encoding="utf-8",
    )
    weakened_report = build_parity_report(sqlite_migrations, postgres_migrations)
    assert weakened_report["status"] == "fail"
    assert any(
        "support_type in ('supporting', 'contradicting')" in issue
        for issue in weakened_report["contract_issues"]
    )

    shutil.rmtree(postgres_migrations)
    shutil.copytree(
        source_root / "src" / "memcore" / "storage" / "postgres" / "migrations",
        postgres_migrations,
    )
    postgres_wp02 = postgres_migrations / "0024_semantic_coverage_audit.sql"
    postgres_wp02.write_text(
        postgres_wp02.read_text(encoding="utf-8").replace(
            "proposal_uuid TEXT UNIQUE,",
            "proposal_uuid TEXT,",
            1,
        ),
        encoding="utf-8",
    )
    uniqueness_report = build_parity_report(sqlite_migrations, postgres_migrations)
    assert uniqueness_report["status"] == "fail"
    assert any(
        "proposal_uuid text unique" in issue for issue in uniqueness_report["contract_issues"]
    )


def _median_planner_ms(value: CoveragePlannerInput, *, repeats: int) -> float:
    samples: list[float] = []
    for _ in range(repeats):
        started = perf_counter()
        plan_candidate_coverage(value)
        samples.append((perf_counter() - started) * 1000)
    return median(samples)


def _planner_case(count: int, *, with_closure: bool = False) -> CoveragePlannerInput:
    parts = [f"Preference {index} is enabled." for index in range(count)]
    raw = " ".join(parts)
    units: list[dict[str, Any]] = []
    references: list[dict[str, Any]] = []
    relations: list[dict[str, Any]] = []
    authorities: list[PersistedUnitAuthority] = []
    cursor = 0
    for index, part in enumerate(parts):
        start = cursor
        end = start + len(part)
        cursor = end + 1
        unit_id = f"unit-{index}"
        units.append(_unit(unit_id=unit_id, text=part, raw_start=start, raw_end=end))
        authorities.append(_authority(start, end, index))
        if with_closure:
            marker_end = start + len("Preference")
            references.append(
                {
                    "id": f"reference-{index}",
                    "source_unit_id": unit_id,
                    "marker_start": start,
                    "marker_end": marker_end,
                    "marker_evidence": "Preference",
                    "status": "resolved",
                    "candidate_referent_ids": [f"current_unit:{unit_id}"],
                    "selected_referent_id": f"current_unit:{unit_id}",
                }
            )
            relations.append(
                {
                    "id": f"relation-{index}",
                    "relation_type": "elaborates",
                    "source_unit_id": unit_id,
                    "target_referent_id": f"current_unit:{unit_id}",
                    "evidence_start": start,
                    "evidence_end": end,
                    "evidence": part,
                }
            )
    analysis = _analysis(raw, units, references=references, relations=relations)
    return _planner_input(raw, analysis, tuple(authorities))


def _analysis(
    raw: str,
    units: list[dict[str, Any]],
    *,
    references: list[dict[str, Any]] | None = None,
    relations: list[dict[str, Any]] | None = None,
) -> SemanticAnalysisV1Output:
    del raw
    return SemanticAnalysisV1Output.model_validate(
        {
            "schema_version": "1.0",
            "prompt_id": "memorist.semantic_candidate_analysis",
            "prompt_version": "1.1",
            "status": "ok",
            "warnings": [],
            "semantic_units": units,
            "references": references or [],
            "relations": relations or [],
        }
    )


def _unit(
    *,
    unit_id: str,
    text: str,
    raw_start: int,
    raw_end: int,
) -> dict[str, Any]:
    return {
        "id": unit_id,
        "raw_start": raw_start,
        "raw_end": raw_end,
        "evidence": text,
        "proposition": text,
        "unit_type": "statement",
        "durability": "durable",
        "polarity": "affirmed",
        "epistemic_status": "asserted",
    }


def _authority(start: int, end: int, index: int) -> PersistedUnitAuthority:
    return PersistedUnitAuthority(
        text_unit_uuid=f"text-unit-{index}",
        raw_start=start,
        raw_end=end,
        annotation_uuid=f"annotation-{index}",
        gate_decision_uuid=f"gate-{index}",
        gate_decision="analyze",
        route_uuid=f"route-{index}",
        route_type="project_context",
        route_status="ready",
        privacy_ceiling="normal",
        privacy_storage_allowed=True,
    )


def _planner_input(
    raw: str,
    analysis: SemanticAnalysisV1Output,
    authorities: tuple[PersistedUnitAuthority, ...],
) -> CoveragePlannerInput:
    return CoveragePlannerInput(
        message_uuid="00000000-0000-4000-8000-000000000001",
        message_version_uuid="00000000-0000-4000-8000-000000000002",
        message_role="user",
        processing_run_uuid="00000000-0000-4000-8000-000000000003",
        current_raw_text=raw,
        text_envelope=build_envelope(raw).as_dict(),
        semantic_analysis=analysis,
        accepted_unit_ids=tuple(unit.id for unit in analysis.semantic_units),
        accepted_reference_indexes=tuple(range(len(analysis.references))),
        accepted_relation_indexes=tuple(range(len(analysis.relations))),
        authorities=authorities,
        semantic_prompt_execution_uuid="00000000-0000-4000-8000-000000000004",
        semantic_contract_hash=SEMANTIC_CANDIDATE_V1_CONTRACT.contract_hash,
        bounded_context_items=(),
        imported_record=False,
        route_mapping_version=ROUTE_CANDIDATE_MAPPING_VERSION,
        provenance_policy_version=PROVENANCE_POLICY_VERSION,
        privacy_policy_version="memorist.privacy.policy.v1",
    )


def _sha256(value: str) -> str:
    import hashlib

    return hashlib.sha256(value.encode("utf-8")).hexdigest()
