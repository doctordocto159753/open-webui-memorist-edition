from __future__ import annotations

import ast
import copy
import hashlib
from pathlib import Path
from typing import Literal

import pytest

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
from memcore.memory_worker.semantic_contract import BoundedContextItem
from memcore.textsemantics.result import build_envelope

ROOT = Path(__file__).resolve().parents[1]


def _analysis(
    raw: str,
    *,
    unit_id: str = "unit-model-id",
    unit_type: str = "statement",
    durability: str = "durable",
    epistemic_status: str = "asserted",
    polarity: str = "affirmed",
    warnings: list[str] | None = None,
) -> SemanticAnalysisV1Output:
    return SemanticAnalysisV1Output.model_validate(
        {
            "schema_version": "1.0",
            "prompt_id": "memorist.semantic_candidate_analysis",
            "prompt_version": "1.0",
            "status": "ok",
            "warnings": warnings or [],
            "semantic_units": [
                {
                    "id": unit_id,
                    "raw_start": 0,
                    "raw_end": len(raw),
                    "evidence": raw,
                    "proposition": raw,
                    "unit_type": unit_type,
                    "durability": durability,
                    "polarity": polarity,
                    "epistemic_status": epistemic_status,
                }
            ],
            "references": [],
            "relations": [],
        }
    )


def _authority(
    raw: str,
    *,
    gate: str | None = "analyze",
    route: str | None = "project_context",
    route_status: str | None = "ready",
    privacy: Literal["normal", "sensitive", "secret"] = "normal",
    privacy_allowed: bool = True,
    suffix: str = "a",
) -> PersistedUnitAuthority:
    return PersistedUnitAuthority(
        text_unit_uuid=f"text-{suffix}",
        raw_start=0,
        raw_end=len(raw),
        annotation_uuid=f"annotation-{suffix}",
        gate_decision_uuid=f"gate-{suffix}" if gate is not None else None,
        gate_decision=gate,
        route_uuid=f"route-{suffix}" if route is not None else None,
        route_type=route,
        route_status=route_status,
        privacy_ceiling=privacy,
        privacy_storage_allowed=privacy_allowed,
    )


def _planner_input(
    raw: str,
    *,
    analysis: SemanticAnalysisV1Output | None = None,
    authorities: tuple[PersistedUnitAuthority, ...] | None = None,
    message_role: Literal["user", "assistant", "tool", "system"] = "user",
    prompt_execution_uuid: str | None = "prompt-run-a",
) -> CoveragePlannerInput:
    resolved = analysis or _analysis(raw)
    return CoveragePlannerInput(
        message_uuid="message-a",
        message_version_uuid=None,
        message_role=message_role,
        processing_run_uuid="processing-a",
        current_raw_text=raw,
        text_envelope=build_envelope(raw).as_dict(),
        semantic_analysis=resolved,
        accepted_unit_ids=tuple(unit.id for unit in resolved.semantic_units),
        accepted_reference_indexes=tuple(range(len(resolved.references))),
        accepted_relation_indexes=tuple(range(len(resolved.relations))),
        authorities=authorities if authorities is not None else (_authority(raw),),
        semantic_prompt_execution_uuid=prompt_execution_uuid,
        semantic_contract_hash=SEMANTIC_CANDIDATE_V1_CONTRACT.contract_hash,
        bounded_context_items=(),
        imported_record=False,
        route_mapping_version=ROUTE_CANDIDATE_MAPPING_VERSION,
        provenance_policy_version=PROVENANCE_POLICY_VERSION,
        privacy_policy_version="memorist.privacy.policy.v1",
    )


@pytest.mark.parametrize(
    ("gate", "expected"),
    [
        ("discard", CoverageDisposition.REJECTED_BY_GATE),
        ("retain_raw_only", CoverageDisposition.REJECTED_BY_GATE),
        ("manual_review", CoverageDisposition.NEEDS_REVIEW),
        (None, CoverageDisposition.NEEDS_REVIEW),
    ],
)
def test_gate_precedence_is_fail_closed(gate: str | None, expected: CoverageDisposition) -> None:
    raw = "Backups stay enabled."
    plan, proposals = plan_candidate_coverage(
        _planner_input(raw, authorities=(_authority(raw, gate=gate),))
    )
    assert plan.items[0].disposition is expected
    assert proposals == ()


@pytest.mark.parametrize(
    ("unit_type", "durability", "epistemic", "expected"),
    [
        ("instruction", "transient", "asserted", CoverageDisposition.TRANSIENT_INSTRUCTION),
        ("statement", "context_only", "asserted", CoverageDisposition.CONTEXT_ONLY),
        ("question", "durable", "questioned", CoverageDisposition.CONTEXT_ONLY),
        ("statement", "durable", "hypothetical", CoverageDisposition.CONTEXT_ONLY),
        ("statement", "unknown", "asserted", CoverageDisposition.NEEDS_REVIEW),
        ("statement", "durable", "unknown", CoverageDisposition.NEEDS_REVIEW),
    ],
)
def test_semantic_policy_precedence(
    unit_type: str,
    durability: str,
    epistemic: str,
    expected: CoverageDisposition,
) -> None:
    raw = "Should backups stay enabled?"
    analysis = _analysis(
        raw,
        unit_type=unit_type,
        durability=durability,
        epistemic_status=epistemic,
    )
    plan, proposals = plan_candidate_coverage(_planner_input(raw, analysis=analysis))
    assert plan.items[0].disposition is expected
    assert proposals == ()


def test_durable_hedged_unit_preserves_axes_and_creates_one_proposal() -> None:
    raw = "Backups probably stay enabled."
    analysis = _analysis(raw, epistemic_status="hedged")
    plan, proposals = plan_candidate_coverage(_planner_input(raw, analysis=analysis))
    assert plan.items[0].disposition is CoverageDisposition.DURABLE_CANDIDATE
    assert len(proposals) == 1
    assert proposals[0].epistemic_status == "hedged"
    assert proposals[0].polarity == "affirmed"
    assert proposals[0].automatic_candidate_creation_allowed is True


def test_unresolved_reference_blocks_candidate() -> None:
    raw = "It stays enabled."
    payload = _analysis(raw).model_dump(mode="json")
    payload["references"] = [
        {
            "id": "reference-any",
            "source_unit_id": "unit-model-id",
            "marker_start": 0,
            "marker_end": 2,
            "marker_evidence": "It",
            "status": "unresolved",
            "candidate_referent_ids": [],
            "selected_referent_id": None,
        }
    ]
    plan, proposals = plan_candidate_coverage(
        _planner_input(raw, analysis=SemanticAnalysisV1Output.model_validate(payload))
    )
    assert plan.items[0].disposition is CoverageDisposition.UNRESOLVED_REFERENCE
    assert proposals == ()


def test_cross_boundary_and_sensitivity_require_review() -> None:
    raw = "Alpha and Beta are enabled."
    first = _authority(raw, suffix="a").model_copy(update={"raw_end": 9})
    second = _authority(raw, suffix="b").model_copy(update={"raw_start": 6})
    plan, _ = plan_candidate_coverage(_planner_input(raw, authorities=(first, second)))
    assert plan.status == "needs_review"
    assert plan.items[0].disposition is CoverageDisposition.NEEDS_REVIEW

    sensitive = _authority(raw, privacy="sensitive")
    plan, proposals = plan_candidate_coverage(_planner_input(raw, authorities=(sensitive,)))
    assert plan.items[0].disposition is CoverageDisposition.NEEDS_REVIEW
    assert proposals == ()


def test_unknown_route_is_unsupported_and_missing_route_needs_review() -> None:
    raw = "Backups stay enabled."
    unknown_plan, _ = plan_candidate_coverage(
        _planner_input(raw, authorities=(_authority(raw, route="not_a_route"),))
    )
    assert unknown_plan.items[0].disposition is CoverageDisposition.UNSUPPORTED
    missing_plan, _ = plan_candidate_coverage(
        _planner_input(raw, authorities=(_authority(raw, route=None),))
    )
    assert missing_plan.items[0].disposition is CoverageDisposition.NEEDS_REVIEW


def test_assistant_context_needs_explicit_current_user_ratification() -> None:
    raw = "Yes, it stays enabled."
    context_text = "Backups stay enabled."
    context = BoundedContextItem(
        context_item_id="assistant-context",
        user_uuid="user-a",
        session_uuid="session-a",
        workspace_uuid=None,
        project_uuid=None,
        message_uuid="prior-assistant-message",
        message_version_uuid=None,
        text_unit_uuid="prior-assistant-unit",
        role="assistant",
        turn_index=1,
        unit_index=0,
        raw_start=0,
        raw_end=len(context_text),
        text=context_text,
        raw_text_hash=hashlib.sha256(context_text.encode()).hexdigest(),
        source_authority_ceiling="assistant_claim",
    )
    payload = _analysis(raw).model_dump(mode="json")
    marker_start = raw.index("it")
    payload["references"] = [
        {
            "id": "model-reference",
            "source_unit_id": "unit-model-id",
            "marker_start": marker_start,
            "marker_end": marker_start + 2,
            "marker_evidence": "it",
            "status": "resolved",
            "candidate_referent_ids": ["prior_context:assistant-context"],
            "selected_referent_id": "prior_context:assistant-context",
        }
    ]
    unresolved_authority = _planner_input(
        raw, analysis=SemanticAnalysisV1Output.model_validate(payload)
    ).model_copy(update={"bounded_context_items": (context,)})
    plan, proposals = plan_candidate_coverage(unresolved_authority)
    assert plan.items[0].disposition is CoverageDisposition.NEEDS_REVIEW
    assert proposals == ()

    payload["relations"] = [
        {
            "id": "model-relation",
            "relation_type": "ratifies",
            "source_unit_id": "unit-model-id",
            "target_referent_id": "prior_context:assistant-context",
            "evidence_start": 0,
            "evidence_end": len(raw),
            "evidence": raw,
        }
    ]
    ratified = _planner_input(
        raw, analysis=SemanticAnalysisV1Output.model_validate(payload)
    ).model_copy(update={"bounded_context_items": (context,)})
    plan, proposals = plan_candidate_coverage(ratified)
    assert plan.items[0].disposition is CoverageDisposition.DURABLE_CANDIDATE
    assert proposals[0].source_authority == "user_explicit"
    assert proposals[0].context_lineage[0]["role"] == "assistant"
    assert "text" not in proposals[0].context_lineage[0]


def test_two_units_get_exactly_two_items_and_uncovered_material_is_explicit() -> None:
    raw = "Alpha is enabled. Beta is pending."
    payload = _analysis(raw).model_dump(mode="json")
    payload["semantic_units"] = [
        {
            **payload["semantic_units"][0],
            "id": "alpha",
            "raw_end": 17,
            "evidence": raw[:17],
            "proposition": "Alpha is enabled",
        },
        {
            **payload["semantic_units"][0],
            "id": "beta",
            "raw_start": 18,
            "raw_end": len(raw),
            "evidence": raw[18:],
            "proposition": "Beta is pending",
        },
    ]
    analysis = SemanticAnalysisV1Output.model_validate(payload)
    value = _planner_input(raw, analysis=analysis)
    plan, proposals = plan_candidate_coverage(value)
    assert len([item for item in plan.items if item.semantic_unit_id]) == 2
    assert len(proposals) == 2

    omitted_payload = copy.deepcopy(payload)
    omitted_payload["semantic_units"] = [omitted_payload["semantic_units"][0]]
    omitted = SemanticAnalysisV1Output.model_validate(omitted_payload)
    plan, _ = plan_candidate_coverage(_planner_input(raw, analysis=omitted))
    uncovered = [item for item in plan.items if item.semantic_unit_id is None]
    assert len(uncovered) == 1
    assert uncovered[0].reason_codes == ("uncovered_material",)
    assert raw[uncovered[0].raw_start : uncovered[0].raw_end] == "Beta is pending"


def test_proposal_identity_excludes_random_lineage_model_ids_and_warnings() -> None:
    raw = "Backups stay enabled."
    first = _planner_input(raw)
    first_plan, first_proposals = plan_candidate_coverage(first)

    changed_payload = first.semantic_analysis.model_dump(mode="json")
    changed_payload["warnings"] = ["non-authoritative"]
    changed_payload["semantic_units"][0]["id"] = "different-model-id"
    changed = first.model_copy(
        update={
            "processing_run_uuid": "processing-random-b",
            "semantic_prompt_execution_uuid": "prompt-random-b",
            "semantic_analysis": SemanticAnalysisV1Output.model_validate(changed_payload),
            "accepted_unit_ids": ("different-model-id",),
            "authorities": (_authority(raw, suffix="random-b"),),
        }
    )
    second_plan, second_proposals = plan_candidate_coverage(changed)
    assert first_proposals[0].proposal_id == second_proposals[0].proposal_id
    assert (
        first_proposals[0].semantic_unit_fingerprint
        == second_proposals[0].semantic_unit_fingerprint
    )
    assert first_plan.coverage_hash != second_plan.coverage_hash


def test_coverage_package_has_no_forbidden_runtime_dependencies() -> None:
    package = ROOT / "src/memcore/memory_worker/semantic/coverage"
    forbidden = {
        "sqlite3",
        "psycopg",
        "sqlalchemy",
        "memcore.repositories",
        "memcore.storage",
        "memcore.retrieval",
        "memcore.embedding",
        "memcore.graph",
        "memcore.memory_worker.providers",
    }
    for path in package.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        } | {str(node.module) for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
        assert not any(
            imported == banned or imported.startswith(f"{banned}.")
            for imported in imports
            for banned in forbidden
        ), path
