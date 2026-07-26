"""Canonical processing-stage status vocabulary.

New writes use exactly four values. Historical aliases remain readable at the
repository boundary so an additive migration does not rewrite existing rows.
"""

from __future__ import annotations

from enum import StrEnum


class StageStatus(StrEnum):
    OK = "ok"
    ABSTAINED = "abstained"
    FAILED_OPEN = "failed_open"
    FAILED = "failed"


CANONICAL_STAGE_STATUSES = frozenset(status.value for status in StageStatus)

_HISTORICAL_STAGE_STATUS_ALIASES = {
    "success": StageStatus.OK,
    "succeeded": StageStatus.OK,
    "abstain": StageStatus.ABSTAINED,
    "disabled": StageStatus.ABSTAINED,
    "fallback": StageStatus.FAILED_OPEN,
    "degraded": StageStatus.FAILED_OPEN,
    "error": StageStatus.FAILED,
}


def normalize_stage_status(value: object, *, historical: bool = True) -> StageStatus:
    """Return a canonical status or fail closed for an unknown value."""

    text = str(value or "").strip().lower()
    try:
        return StageStatus(text)
    except ValueError:
        if historical and text in _HISTORICAL_STAGE_STATUS_ALIASES:
            return _HISTORICAL_STAGE_STATUS_ALIASES[text]
        raise ValueError(f"unknown processing stage status: {text or '(empty)'}") from None


def stage_status_for_output(
    output_status: object,
    *,
    fallback_used: bool,
    provider_failed: bool,
) -> StageStatus:
    """Project contract output and fallback truth onto the stage vocabulary."""

    if fallback_used and provider_failed:
        return StageStatus.FAILED_OPEN
    text = str(output_status or "ok").strip().lower()
    if text in {"abstain", "abstained"}:
        return StageStatus.ABSTAINED
    if text in {"reject", "error", "failed"}:
        return StageStatus.FAILED
    return StageStatus.OK
