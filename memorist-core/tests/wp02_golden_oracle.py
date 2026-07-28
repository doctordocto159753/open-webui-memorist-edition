"""Implementation-independent WP02 golden-corpus loader and acceptance oracle.

Expected semantic axes and dispositions live in the versioned fixture.  This
module deliberately does not import the production planner, identity code, or
runtime orchestration service.  Tests may pass production results into these
functions, but the expected answer never comes from production code.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

CORPUS_PATH = Path(__file__).parent / "fixtures" / "wp02" / "golden-corpus-v1.ijson"
CORPUS_VERSION = "memorist.wp02.golden_corpus.v1"


class GoldenOracleMismatch(AssertionError):
    """The observed result differs from the independently authored corpus."""


def load_golden_corpus() -> dict[str, Any]:
    value = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
    if value.get("corpus_version") != CORPUS_VERSION:
        raise GoldenOracleMismatch("unexpected WP02 corpus version")
    return value


def case_by_id(case_id: str) -> dict[str, Any]:
    for case in load_golden_corpus()["cases"]:
        if case["case_id"] == case_id:
            return copy.deepcopy(case)
    raise KeyError(case_id)


def context_case_by_id(case_id: str) -> dict[str, Any]:
    for case in load_golden_corpus()["context_cases"]:
        if case["case_id"] == case_id:
            return copy.deepcopy(case)
    raise KeyError(case_id)


def assert_exact_evidence(case: dict[str, Any]) -> None:
    raw = case.get("raw_text", case.get("current_raw_text"))
    if not isinstance(raw, str):
        raise GoldenOracleMismatch("case has no exact current raw text")
    output = case["semantic_output"]
    for unit in output["semantic_units"]:
        _assert_slice(raw, unit["raw_start"], unit["raw_end"], unit["evidence"], unit["id"])
    for reference in output["references"]:
        _assert_slice(
            raw,
            reference["marker_start"],
            reference["marker_end"],
            reference["marker_evidence"],
            reference["id"],
        )
    for relation in output["relations"]:
        _assert_slice(
            raw,
            relation["evidence_start"],
            relation["evidence_end"],
            relation["evidence"],
            relation["id"],
        )


def assert_semantic_output(case: dict[str, Any], observed: dict[str, Any]) -> None:
    """Compare semantic meaning with fixture expectations, not implementation output."""

    expected_output = case["semantic_output"]
    expected = case["expected"]
    observed_ids = [unit.get("id") for unit in observed.get("semantic_units", [])]
    if observed_ids != expected["unit_ids"]:
        raise GoldenOracleMismatch(
            f"{case['case_id']}: expected units {expected['unit_ids']}, got {observed_ids}"
        )
    expected_units = {unit["id"]: unit for unit in expected_output["semantic_units"]}
    observed_units = {unit["id"]: unit for unit in observed["semantic_units"]}
    semantic_fields = (
        "raw_start",
        "raw_end",
        "evidence",
        "proposition",
        "unit_type",
        "durability",
        "polarity",
        "epistemic_status",
    )
    for unit_id in expected["unit_ids"]:
        for field in semantic_fields:
            if observed_units[unit_id].get(field) != expected_units[unit_id][field]:
                raise GoldenOracleMismatch(
                    f"{case['case_id']}:{unit_id}: semantic field {field} differs"
                )

    expected_references = {
        reference["id"]: reference for reference in expected_output.get("references", [])
    }
    observed_references = {
        reference.get("id"): reference for reference in observed.get("references", [])
    }
    if set(observed_references) != set(expected_references):
        raise GoldenOracleMismatch(f"{case['case_id']}: reference set differs")
    for reference_id, reference in expected_references.items():
        actual = observed_references[reference_id]
        for field in (
            "source_unit_id",
            "marker_start",
            "marker_end",
            "marker_evidence",
            "status",
            "candidate_referent_ids",
            "selected_referent_id",
        ):
            if actual.get(field) != reference[field]:
                raise GoldenOracleMismatch(
                    f"{case['case_id']}:{reference_id}: reference field {field} differs"
                )
        if (
            reference["status"] in {"ambiguous", "unresolved"}
            and actual.get("selected_referent_id") is not None
        ):
            raise GoldenOracleMismatch(
                f"{case['case_id']}:{reference_id}: unresolved reference guessed a target"
            )

    expected_relations = {
        relation["id"]: relation for relation in expected_output.get("relations", [])
    }
    observed_relations = {
        relation.get("id"): relation for relation in observed.get("relations", [])
    }
    if observed_relations != expected_relations:
        raise GoldenOracleMismatch(f"{case['case_id']}: relation set differs")
    assert_exact_evidence({**case, "semantic_output": observed})


def assert_coverage_plan(
    case: dict[str, Any],
    observed_items: list[dict[str, Any]],
    observed_proposal_ids: list[str],
) -> None:
    """Assert explicit unit coverage; uncovered-material audit items are orthogonal."""

    expected = case["expected"]
    unit_items = {
        item.get("semantic_unit_id"): item
        for item in observed_items
        if item.get("semantic_unit_id") is not None
    }
    if set(unit_items) != set(expected["unit_ids"]):
        raise GoldenOracleMismatch(
            f"{case['case_id']}: unit coverage differs; silent omission or fabrication"
        )
    for unit_id, disposition in expected["dispositions"].items():
        if unit_items[unit_id].get("disposition") != disposition:
            raise GoldenOracleMismatch(
                f"{case['case_id']}:{unit_id}: expected {disposition}, "
                f"got {unit_items[unit_id].get('disposition')}"
            )
        proposal_id = unit_items[unit_id].get("proposal_id")
        if disposition == "durable_candidate" and proposal_id is None:
            raise GoldenOracleMismatch(f"{case['case_id']}:{unit_id}: proposal omitted")
        if disposition != "durable_candidate" and proposal_id is not None:
            raise GoldenOracleMismatch(
                f"{case['case_id']}:{unit_id}: non-durable item gained a proposal"
            )
    if len(observed_proposal_ids) != expected["proposal_count"]:
        raise GoldenOracleMismatch(
            f"{case['case_id']}: expected {expected['proposal_count']} proposals, "
            f"got {len(observed_proposal_ids)}"
        )


def _assert_slice(raw: str, start: int, end: int, evidence: str, label: str) -> None:
    if not isinstance(start, int) or not isinstance(end, int) or start >= end:
        raise GoldenOracleMismatch(f"{label}: invalid expected span")
    if raw[start:end] != evidence:
        raise GoldenOracleMismatch(f"{label}: evidence is not the exact raw slice")
