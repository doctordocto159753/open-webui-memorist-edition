"""Validate a model's semantic analysis against the immutable raw text.

This is the deterministic half of the split. The model decides **what the text
says**; this module decides **whether the model's answer is admissible** -- and
admissibility is a question about characters and offsets, which code can settle
exactly.

Every check here is of that kind:

* does the declared span lie inside the text?
* is the quoted evidence byte-identical to that slice, or was it paraphrased,
  reformatted, or invented?
* do two units claim the same characters?
* does a reference point at a unit that exists?
* was the selected referent among the candidates the model itself offered?

None of these require judgement about meaning, and all of them catch the
failure that actually matters: an analysis that reads plausibly while pointing
at text that is not there.

A violation is never repaired by guessing. The caller either drops the offending
unit or abstains for the whole message -- see ``SemanticFallback``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

VALIDATION_CONTRACT_VERSION = "memorist.text.semantic_validation.v1"


class SemanticFallback(StrEnum):
    """What to do when no trustworthy semantic analysis exists.

    All three are fail-closed. None of them creates a memory, because the
    alternative -- letting deterministic code reconstruct meaning from
    hand-written rules when the model is unavailable -- is how a fragile parser
    gets built by accident.
    """

    #: Keep the raw message and derive nothing from it.
    RETAIN_RAW_ONLY = "retain_raw_only"
    #: Keep it and mark it for a human.
    NEEDS_REVIEW = "needs_review"
    #: Decline to analyse; no candidate, no annotation.
    ABSTAIN = "abstain"


class Violation(StrEnum):
    """Why a unit or reference was rejected."""

    SPAN_OUT_OF_RANGE = "span_out_of_range"
    SPAN_INVERTED = "span_inverted"
    EVIDENCE_NOT_A_SLICE = "evidence_not_a_slice"
    EVIDENCE_MISSING = "evidence_missing"
    UNITS_OVERLAP = "units_overlap"
    DUPLICATE_UNIT_ID = "duplicate_unit_id"
    UNKNOWN_UNIT_ID = "unknown_unit_id"
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
    """The admissible subset, plus everything that was thrown out and why."""

    accepted_unit_ids: tuple[str, ...] = ()
    accepted_reference_indexes: tuple[int, ...] = ()
    rejections: tuple[Rejection, ...] = field(default_factory=tuple)
    contract_version: str = VALIDATION_CONTRACT_VERSION

    @property
    def ok(self) -> bool:
        return not self.rejections

    @property
    def fallback(self) -> SemanticFallback | None:
        """Fail-closed outcome when nothing usable survived validation."""

        if self.accepted_unit_ids:
            return None
        return SemanticFallback.RETAIN_RAW_ONLY if self.rejections else SemanticFallback.ABSTAIN


def validate_semantic_analysis(raw: str, payload: Mapping[str, Any]) -> ValidationReport:
    """Check a model's ``semantic_units`` / ``references`` against ``raw``.

    Returns which records are admissible rather than raising, so a caller can
    keep the good units and drop the bad ones instead of discarding a whole
    message because of one bad span.
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
    if status == "resolved":
        if not isinstance(target, str) or not target:
            return Rejection(
                Violation.RESOLVED_WITHOUT_TARGET, "reference", label, "status=resolved, no target"
            )
        if target not in accepted:
            return Rejection(
                Violation.UNKNOWN_UNIT_ID, "reference", label, f"target {target!r} is not a unit"
            )
        # A model may only choose from the options it itself put forward.
        # Picking outside its own candidate list means the choice came from
        # somewhere the record does not account for.
        offered = _sequence(reference.get("candidate_unit_ids"))
        if offered and target not in offered:
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
    """Evidence must be the exact slice, never a tidied-up version of it.

    A model that returns text it reformatted -- collapsed whitespace, stripped a
    ZWNJ, fixed a typo -- has produced something that no longer addresses the
    stored message. Accepting it would put unauditable text into evidence.
    """

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
