"""Validate model evidence against immutable raw text.

This module is intentionally an **evidence-integrity validator**, not the strict
semantic output contract. The model decides what the text says; WP02 must bind
that output to one closed typed schema before this validator runs. This module
then decides whether the model's citations and reference links are admissible.

Every check here is character- or identity-based:

* does the declared span lie inside the text?
* is quoted evidence byte-identical to that slice?
* do semantic units overlap or reuse an id?
* does a reference point at an accepted unit?
* did a resolved reference select from a non-empty candidate list?

A violation is never repaired by guessing. The caller drops the offending
record or uses the fail-closed fallback.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

EVIDENCE_VALIDATION_CONTRACT_VERSION = "memorist.text.semantic_evidence_validation.v1"
# Compatibility export for the pre-correction WP01 API. New code should use the
# explicit evidence-integrity name above.
VALIDATION_CONTRACT_VERSION = EVIDENCE_VALIDATION_CONTRACT_VERSION


class SemanticFallback(StrEnum):
    """What to do when no trustworthy semantic analysis exists."""

    RETAIN_RAW_ONLY = "retain_raw_only"
    NEEDS_REVIEW = "needs_review"
    ABSTAIN = "abstain"


class Violation(StrEnum):
    """Why a semantic unit or reference was rejected."""

    SPAN_OUT_OF_RANGE = "span_out_of_range"
    SPAN_INVERTED = "span_inverted"
    EVIDENCE_NOT_A_SLICE = "evidence_not_a_slice"
    EVIDENCE_MISSING = "evidence_missing"
    UNITS_OVERLAP = "units_overlap"
    DUPLICATE_UNIT_ID = "duplicate_unit_id"
    UNKNOWN_UNIT_ID = "unknown_unit_id"
    CANDIDATE_LIST_REQUIRED = "candidate_list_required"
    UNKNOWN_CANDIDATE_UNIT_ID = "unknown_candidate_unit_id"
    REFERENT_NOT_A_CANDIDATE = "referent_not_a_candidate"
    RESOLVED_WITHOUT_TARGET = "resolved_without_target"
    MALFORMED_RECORD = "malformed_record"


@dataclass(frozen=True)
class Rejection:
    """One rejected record, with the reason and where it came from."""

    violation: Violation
    record_kind: str
    record_id: str | None
    detail: str


@dataclass(frozen=True)
class ValidationReport:
    """The evidence-admissible subset, plus every rejection and reason."""

    accepted_unit_ids: tuple[str, ...] = ()
    accepted_reference_indexes: tuple[int, ...] = ()
    rejections: tuple[Rejection, ...] = field(default_factory=tuple)
    contract_version: str = EVIDENCE_VALIDATION_CONTRACT_VERSION

    @property
    def ok(self) -> bool:
        return not self.rejections

    @property
    def fallback(self) -> SemanticFallback | None:
        """Fail-closed outcome when nothing usable survived validation."""

        if self.accepted_unit_ids:
            return None
        return SemanticFallback.RETAIN_RAW_ONLY if self.rejections else SemanticFallback.ABSTAIN


# Explicit name for the final authority boundary.
EvidenceValidationReport = ValidationReport


def validate_semantic_evidence(raw: str, payload: Mapping[str, Any]) -> ValidationReport:
    """Check model evidence and references against ``raw``.

    This does **not** validate semantic enum values such as ``unit_type``,
    ``polarity``, ``durability``, or ``epistemic_status``. WP02 must perform
    strict closed-schema validation first. This function returns the
    evidence-admissible subset rather than raising, so one bad citation does not
    force the caller to trust or reconstruct it.
    """

    rejections: list[Rejection] = []
    units = _sequence(payload.get("semantic_units"))
    references = _sequence(payload.get("references"))

    accepted: dict[str, tuple[int, int]] = {}
    for position, unit in enumerate(units):
        outcome = _validate_unit(raw, unit, position, accepted)
        if isinstance(outcome, Rejection):
            rejections.append(outcome)
            continue
        unit_id, span = outcome
        accepted[unit_id] = span

    accepted_references: list[int] = []
    for position, reference in enumerate(references):
        rejection = _validate_reference(raw, reference, position, accepted)
        if rejection is not None:
            rejections.append(rejection)
            continue
        accepted_references.append(position)

    return ValidationReport(
        accepted_unit_ids=tuple(accepted),
        accepted_reference_indexes=tuple(accepted_references),
        rejections=tuple(rejections),
    )


def validate_semantic_analysis(raw: str, payload: Mapping[str, Any]) -> ValidationReport:
    """Compatibility alias for :func:`validate_semantic_evidence`.

    The older name implied that this module validated semantic meaning and enum
    structure. It never did. Keeping the alias avoids breaking callers while the
    explicit name makes the WP01/WP02 boundary auditable.
    """

    return validate_semantic_evidence(raw, payload)


def _validate_unit(
    raw: str,
    unit: Any,
    position: int,
    accepted: dict[str, tuple[int, int]],
) -> tuple[str, tuple[int, int]] | Rejection:
    if not isinstance(unit, Mapping):
        return Rejection(Violation.MALFORMED_RECORD, "semantic_unit", None, f"index {position}")
    unit_id = unit.get("id")
    if not isinstance(unit_id, str) or not unit_id:
        return Rejection(
            Violation.MALFORMED_RECORD, "semantic_unit", None, f"index {position}: missing id"
        )
    if unit_id in accepted:
        return Rejection(Violation.DUPLICATE_UNIT_ID, "semantic_unit", unit_id, "id seen twice")

    span = _span(unit.get("raw_start"), unit.get("raw_end"))
    if span is None:
        return Rejection(
            Violation.MALFORMED_RECORD, "semantic_unit", unit_id, "raw_start/raw_end not integers"
        )
    start, end = span
    if start >= end:
        return Rejection(Violation.SPAN_INVERTED, "semantic_unit", unit_id, f"[{start}, {end})")
    if start < 0 or end > len(raw):
        return Rejection(
            Violation.SPAN_OUT_OF_RANGE,
            "semantic_unit",
            unit_id,
            f"[{start}, {end}) outside [0, {len(raw)})",
        )

    rejection = _check_evidence(raw, unit, unit_id, start, end, "semantic_unit")
    if rejection is not None:
        return rejection

    for other_id, (other_start, other_end) in accepted.items():
        if start < other_end and other_start < end:
            return Rejection(
                Violation.UNITS_OVERLAP,
                "semantic_unit",
                unit_id,
                f"overlaps {other_id} at [{other_start}, {other_end})",
            )
    return unit_id, (start, end)


def _validate_reference(
    raw: str,
    reference: Any,
    position: int,
    accepted: dict[str, tuple[int, int]],
) -> Rejection | None:
    if not isinstance(reference, Mapping):
        return Rejection(Violation.MALFORMED_RECORD, "reference", None, f"index {position}")
    label = str(position)

    span = _span(reference.get("marker_start"), reference.get("marker_end"))
    if span is None:
        return Rejection(
            Violation.MALFORMED_RECORD, "reference", label, "marker_start/marker_end not integers"
        )
    start, end = span
    if start >= end:
        return Rejection(Violation.SPAN_INVERTED, "reference", label, f"[{start}, {end})")
    if start < 0 or end > len(raw):
        return Rejection(
            Violation.SPAN_OUT_OF_RANGE,
            "reference",
            label,
            f"[{start}, {end}) outside [0, {len(raw)})",
        )

    rejection = _check_evidence(
        raw, reference, label, start, end, "reference", key="marker_evidence"
    )
    if rejection is not None:
        return rejection

    status = reference.get("status")
    target = reference.get("target_unit_id")
    offered_raw = reference.get("candidate_unit_ids")
    offered = _sequence(offered_raw)

    for candidate in offered:
        if not isinstance(candidate, str) or candidate not in accepted:
            return Rejection(
                Violation.UNKNOWN_CANDIDATE_UNIT_ID,
                "reference",
                label,
                f"candidate {candidate!r} is not an accepted semantic unit",
            )

    if status == "resolved":
        if not isinstance(target, str) or not target:
            return Rejection(
                Violation.RESOLVED_WITHOUT_TARGET, "reference", label, "status=resolved, no target"
            )
        if target not in accepted:
            return Rejection(
                Violation.UNKNOWN_UNIT_ID, "reference", label, f"target {target!r} is not a unit"
            )
        if not offered:
            return Rejection(
                Violation.CANDIDATE_LIST_REQUIRED,
                "reference",
                label,
                "status=resolved requires a non-empty candidate_unit_ids list",
            )
        if target not in offered:
            return Rejection(
                Violation.REFERENT_NOT_A_CANDIDATE,
                "reference",
                label,
                f"target {target!r} not in its own candidates",
            )
    return None


def _check_evidence(
    raw: str,
    record: Mapping[str, Any],
    label: str,
    start: int,
    end: int,
    kind: str,
    key: str = "evidence",
) -> Rejection | None:
    """Evidence must be the exact slice, never a tidied-up version."""

    evidence = record.get(key)
    if evidence is None:
        return Rejection(Violation.EVIDENCE_MISSING, kind, label, f"no {key}")
    if not isinstance(evidence, str):
        return Rejection(Violation.MALFORMED_RECORD, kind, label, f"{key} is not a string")
    actual = raw[start:end]
    if evidence != actual:
        return Rejection(
            Violation.EVIDENCE_NOT_A_SLICE,
            kind,
            label,
            f"{key}={evidence!r} but raw[{start}:{end}]={actual!r}",
        )
    return None


def _span(start: Any, end: Any) -> tuple[int, int] | None:
    if isinstance(start, bool) or isinstance(end, bool):
        return None
    if not isinstance(start, int) or not isinstance(end, int):
        return None
    return start, end


def _sequence(value: Any) -> list[Any]:
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return list(value)
    return []
