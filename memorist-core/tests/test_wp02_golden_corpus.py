from __future__ import annotations

import copy
import hashlib
import inspect
from typing import Any

import pytest
from pydantic import ValidationError

import wp02_golden_oracle
from memcore.memory_worker.extraction.sensitivity import classify_sensitivity
from memcore.memory_worker.prompts.contracts import (
    SEMANTIC_CANDIDATE_V1_CONTRACT,
    SemanticAnalysisV1Output,
)
from memcore.memory_worker.semantic.coverage import (
    CoverageDisposition,
    CoveragePlannerInput,
    PersistedUnitAuthority,
    plan_candidate_coverage,
)
from memcore.memory_worker.semantic_contract import (
    BoundedContextItem,
    SemanticContextBoundary,
    build_semantic_input,
    validate_semantic_binding,
)
from memcore.models import SensitivityClass
from memcore.textsemantics import build_envelope, contains_token
from wp02_golden_oracle import (
    GoldenOracleMismatch,
    assert_coverage_plan,
    assert_exact_evidence,
    assert_semantic_output,
    case_by_id,
    context_case_by_id,
    load_golden_corpus,
)

SEMANTIC_CONTRACT_HASH = "be215b992503c4c539221f5b8f6d6b2a3661f256e0ae499636a95c13a96319d7"


def test_corpus_is_versioned_explicit_and_independent_of_implementation() -> None:
    corpus = load_golden_corpus()
    assert corpus["corpus_version"] == "memorist.wp02.golden_corpus.v1"
    assert len(corpus["cases"]) == 11
    assert {case["language"] for case in corpus["cases"]} == {"fa", "mixed"}
    source = inspect.getsource(wp02_golden_oracle)
    assert "plan_candidate_coverage" not in source
    assert "semantic.coverage.identity" not in source
    for case in corpus["cases"]:
        assert_exact_evidence(case)


@pytest.mark.parametrize(
    "case_id",
    [case["case_id"] for case in load_golden_corpus()["cases"]],
)
def test_golden_semantic_outputs_pass_strict_and_evidence_contract(case_id: str) -> None:
    case = case_by_id(case_id)
    payload = _semantic_input(case)
    output = case["semantic_output"]
    assert SEMANTIC_CANDIDATE_V1_CONTRACT.validate(output) == []
    report = validate_semantic_binding(payload.model_dump(mode="json"), output)
    assert report.accepted_unit_ids == tuple(case["expected"]["unit_ids"])
    assert report.rejections == ()
    assert_semantic_output(case, output)


@pytest.mark.parametrize(
    ("mutation", "path"),
    [
        ("missing", "semantic_units"),
        ("extra", "message_id"),
        ("enum", "semantic_units.0.unit_type"),
        ("version", "schema_version"),
        ("wrong_type", "semantic_units.0.raw_start"),
    ],
)
def test_strict_contract_mutations_are_rejected(mutation: str, path: str) -> None:
    output = case_by_id("transient-01-current-turn-style")["semantic_output"]
    if mutation == "missing":
        output.pop("semantic_units")
    elif mutation == "extra":
        output["message_id"] = "model-invented"
    elif mutation == "enum":
        output["semantic_units"][0]["unit_type"] = "preference"
    elif mutation == "version":
        output["schema_version"] = "2.0"
    else:
        output["semantic_units"][0]["raw_start"] = "0"
    issues = SEMANTIC_CANDIDATE_V1_CONTRACT.validate(output)
    assert issues
    assert any(issue["path"] == path or issue["path"].startswith(path) for issue in issues)


def test_abstain_with_units_and_plausible_legacy_shape_are_not_false_green() -> None:
    case = case_by_id("transient-01-current-turn-style")
    abstain_with_units = copy.deepcopy(case["semantic_output"])
    abstain_with_units["status"] = "abstain"
    assert SEMANTIC_CANDIDATE_V1_CONTRACT.validate(abstain_with_units) == []
    with pytest.raises(ValueError, match="status=abstain"):
        validate_semantic_binding(
            _semantic_input(case).model_dump(mode="json"),
            abstain_with_units,
        )
    legacy = {
        "schema_version": "1.0",
        "prompt_id": "memorist.semantic_candidate_analysis",
        "prompt_version": "1.1",
        "status": "ok",
        "warnings": [],
        "items": [],
    }
    assert SEMANTIC_CANDIDATE_V1_CONTRACT.validate(legacy)


def test_oracle_detects_silent_unit_omission() -> None:
    case = case_by_id("multi-01-migration-and-preference")
    mutated = copy.deepcopy(case["semantic_output"])
    mutated["semantic_units"].pop()
    with pytest.raises(GoldenOracleMismatch, match="expected units"):
        assert_semantic_output(case, mutated)


def test_oracle_detects_forced_resolution_of_ambiguous_reference() -> None:
    case = case_by_id("reference-02-ambiguous-current-unit")
    mutated = copy.deepcopy(case["semantic_output"])
    reference = mutated["references"][0]
    reference["status"] = "resolved"
    reference["selected_referent_id"] = reference["candidate_referent_ids"][0]
    with pytest.raises(GoldenOracleMismatch, match="reference field status"):
        assert_semantic_output(case, mutated)


@pytest.mark.parametrize(
    "mutation",
    [
        "unknown_unit",
        "unknown_context",
        "relation_endpoint",
        "target_outside_candidates",
        "ambiguous_selected",
        "normalized_evidence",
        "overlap",
        "duplicate_id",
    ],
)
def test_semantic_and_evidence_validator_mutations_fail_closed(mutation: str) -> None:
    case = case_by_id("reference-01-unique-current-unit")
    output = copy.deepcopy(case["semantic_output"])
    if mutation == "unknown_unit":
        output["references"][0]["source_unit_id"] = "missing-unit"
    elif mutation == "unknown_context":
        output["references"][0]["candidate_referent_ids"] = ["prior_context:missing"]
        output["references"][0]["selected_referent_id"] = "prior_context:missing"
    elif mutation == "relation_endpoint":
        output["relations"] = [
            {
                "id": "bad-relation",
                "relation_type": "elaborates",
                "source_unit_id": "missing-unit",
                "target_referent_id": "current_unit:kubuntu-benefit",
                "evidence_start": 37,
                "evidence_end": 43,
                "evidence": case["raw_text"][37:43],
            }
        ]
    elif mutation == "target_outside_candidates":
        output["references"][0]["candidate_referent_ids"] = [
            "current_unit:deferred-benefit-discussion"
        ]
    elif mutation == "ambiguous_selected":
        output["references"][0]["status"] = "ambiguous"
    elif mutation == "normalized_evidence":
        output["semantic_units"][0]["evidence"] = output["semantic_units"][0]["evidence"].replace(
            "Kubuntu", "kubuntu"
        )
    elif mutation == "overlap":
        output["semantic_units"][1]["raw_start"] = 35
        output["semantic_units"][1]["evidence"] = case["raw_text"][35:69]
    else:
        output["semantic_units"][1]["id"] = output["semantic_units"][0]["id"]
    with pytest.raises(ValueError):
        validate_semantic_binding(_semantic_input(case).model_dump(mode="json"), output)


@pytest.mark.parametrize(
    "case_id",
    [case["case_id"] for case in load_golden_corpus()["cases"]],
)
def test_planner_matches_independent_disposition_oracle(case_id: str) -> None:
    case = case_by_id(case_id)
    plan, proposals = plan_candidate_coverage(_planner_input(case))
    assert_coverage_plan(
        case,
        [item.model_dump(mode="json") for item in plan.items],
        [proposal.proposal_id for proposal in proposals],
    )


def test_terminal_gate_mutation_remains_audit_only_after_semantic_analysis() -> None:
    case = case_by_id("multi-01-migration-and-preference")
    planner_input = _planner_input(case)
    rejected_authority = planner_input.authorities[0].model_copy(
        update={"gate_decision": "discard"}
    )
    plan, proposals = plan_candidate_coverage(
        planner_input.model_copy(update={"authorities": (rejected_authority,)})
    )
    unit_items = [item for item in plan.items if item.semantic_unit_id is not None]
    assert len(proposals) == 2
    assert {item.disposition for item in unit_items} == {CoverageDisposition.DURABLE_CANDIDATE}


def test_plan_oracle_detects_post_planner_omission() -> None:
    case = case_by_id("multi-01-migration-and-preference")
    plan, proposals = plan_candidate_coverage(_planner_input(case))
    observed = [
        item.model_dump(mode="json")
        for item in plan.items
        if item.semantic_unit_id != "performance-preference"
    ]
    with pytest.raises(GoldenOracleMismatch, match="silent omission"):
        assert_coverage_plan(
            case,
            observed,
            [proposal.proposal_id for proposal in proposals],
        )


def test_proposal_identity_matches_independent_frozen_vectors_not_self_comparison() -> None:
    case = case_by_id("multi-01-migration-and-preference")
    _plan, proposals = plan_candidate_coverage(_planner_input(case))
    assert {proposal.semantic_unit_id: proposal.proposal_id for proposal in proposals} == {
        "migration": "8978f8a3-97c6-5415-9487-f616dc42d1f9",
        "performance-preference": "cb259341-b4e7-5b0b-bb0e-2248756bab8a",
    }


def test_assistant_ratification_requires_current_user_relation_and_keeps_lineage() -> None:
    case = context_case_by_id("assistant-ratification-positive")
    planner_input = _planner_input(case, context_item=_assistant_context(case))
    plan, proposals = plan_candidate_coverage(planner_input)
    assert len(proposals) == 1
    proposal = proposals[0]
    assert proposal.source_authority == case["expected"]["source_authority"]
    assert proposal.context_lineage
    assert proposal.context_lineage[0]["role"] == "assistant"
    assert proposal.context_lineage[0]["context_item_id"] == "assistant-proposal"
    assert (
        next(item for item in plan.items if item.semantic_unit_id == "ratification-act").disposition
        is CoverageDisposition.DURABLE_CANDIDATE
    )

    no_relation = planner_input.semantic_analysis.model_copy(update={"relations": []})
    mutated = planner_input.model_copy(
        update={"semantic_analysis": no_relation, "accepted_relation_indexes": ()}
    )
    mutated_plan, mutated_proposals = plan_candidate_coverage(mutated)
    assert mutated_proposals == ()
    assert (
        next(
            item for item in mutated_plan.items if item.semantic_unit_id == "ratification-act"
        ).disposition
        is CoverageDisposition.NEEDS_REVIEW
    )


def test_assistant_ceiling_cannot_be_promoted_to_user_authority() -> None:
    case = context_case_by_id("assistant-ratification-positive")
    item = _assistant_context(case).model_dump(mode="json")
    item["source_authority_ceiling"] = "user_explicit"
    with pytest.raises(ValidationError, match="source_authority_ceiling"):
        BoundedContextItem.model_validate(item)


def test_persian_mixed_code_fence_and_exact_offsets_are_covered() -> None:
    corpus = load_golden_corpus()
    sample_ids = {sample["sample_id"] for sample in corpus["evidence_integrity_samples"]}
    assert sample_ids == {
        "zwnj-and-persian-variants",
        "mixed-emoji-combining",
        "code-fence-crlf-typo",
    }
    for sample in corpus["evidence_integrity_samples"]:
        raw = sample["raw_text"]
        envelope = build_envelope(raw)
        assert envelope.raw_text_hash == hashlib.sha256(raw.encode("utf-8")).hexdigest()
        if sample["sample_id"] == "code-fence-crlf-typo":
            key_name = "api_" + "key"
            expected_value = "-".join(("example", "token", "12345678"))
            code = f'```python\r\n{key_name} = "{expected_value}"\r\nprint("teh typo")\r\n```'
            assert raw[raw.index("```") : raw.rindex("```") + 3] == code
            assert any(block.kind.value == "code" for block in envelope.blocks)


def test_wp01_token_boundaries_and_lexical_polarity_non_authority_are_preserved() -> None:
    corpus = load_golden_corpus()
    for regression in corpus["wp01_boundary_regressions"]:
        assert not contains_token(regression["haystack"], regression["needle"])
    negated = case_by_id("polarity-02-explicit-negation")
    envelope = build_envelope(negated["raw_text"]).as_dict()
    assert "polarity" not in envelope
    assert negated["semantic_output"]["semantic_units"][0]["polarity"] == "negated"


def test_secret_detection_in_prose_and_code_is_not_bypassed() -> None:
    corpus = load_golden_corpus()
    samples = {sample["sample_id"]: sample for sample in corpus["security_samples"]}
    assert classify_sensitivity(samples["api-key-prose"]["raw_text"]) is SensitivityClass.SECRET
    assert (
        classify_sensitivity(samples["api-key-code-fence"]["raw_text"]) is SensitivityClass.SECRET
    )


def _semantic_input(
    case: dict[str, Any],
    *,
    context_item: BoundedContextItem | None = None,
) -> Any:
    raw = str(case.get("raw_text", case.get("current_raw_text")))
    items = [context_item] if context_item is not None else []
    return build_semantic_input(
        current_message_uuid="message-current",
        current_message_version_uuid="version-current",
        current_raw_text=raw,
        text_envelope=build_envelope(raw),
        bounded_context_items=items,
        boundary=SemanticContextBoundary(
            user_uuid="user-1",
            session_uuid="session-1",
            workspace_uuid="workspace-1",
            project_uuid="project-1",
            baseline_limit=2,
            effective_limit=2,
            dependency_expansion=False,
        ),
    )


def _planner_input(
    case: dict[str, Any],
    *,
    context_item: BoundedContextItem | None = None,
) -> CoveragePlannerInput:
    semantic_input = _semantic_input(case, context_item=context_item)
    output = SemanticAnalysisV1Output.model_validate(case["semantic_output"])
    report = validate_semantic_binding(
        semantic_input.model_dump(mode="json"),
        output.model_dump(mode="json"),
    )
    raw = semantic_input.current_raw_text
    authority = PersistedUnitAuthority(
        text_unit_uuid="text-unit-current",
        raw_start=0,
        raw_end=len(raw),
        annotation_uuid="annotation-current",
        gate_decision_uuid="gate-current",
        gate_decision="analyze",
        route_uuid="route-current",
        route_type=str(case.get("route_type") or "task_constraint"),
        route_status="ready",
        privacy_ceiling="normal",
        privacy_storage_allowed=True,
    )
    return CoveragePlannerInput(
        message_uuid=semantic_input.current_message_uuid,
        message_version_uuid=semantic_input.current_message_version_uuid,
        message_role="user",
        processing_run_uuid="processing-run-current",
        current_raw_text=raw,
        text_envelope=semantic_input.text_envelope,
        semantic_analysis=output,
        accepted_unit_ids=report.accepted_unit_ids,
        accepted_reference_indexes=report.accepted_reference_indexes,
        accepted_relation_indexes=report.accepted_relation_indexes,
        authorities=(authority,),
        semantic_prompt_execution_uuid="semantic-prompt-current",
        semantic_contract_hash=SEMANTIC_CONTRACT_HASH,
        bounded_context_items=((context_item,) if context_item is not None else ()),
        route_mapping_version="pr4d-route-candidate-mapper-v1",
        provenance_policy_version="pr4d-provenance-policy-v1",
        privacy_policy_version="wp02-privacy-ceiling-v1",
    )


def _assistant_context(case: dict[str, Any]) -> BoundedContextItem:
    text = case["assistant_context"]
    return BoundedContextItem(
        context_item_id="assistant-proposal",
        user_uuid="user-1",
        session_uuid="session-1",
        workspace_uuid="workspace-1",
        project_uuid="project-1",
        message_uuid="message-assistant",
        message_version_uuid="version-assistant",
        text_unit_uuid="unit-assistant",
        role="assistant",
        turn_index=1,
        unit_index=0,
        raw_start=0,
        raw_end=len(text),
        text=text,
        raw_text_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        source_authority_ceiling="assistant_claim",
    )
