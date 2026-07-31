"""Closed deterministic disposition policy for evidence-admissible units."""

from __future__ import annotations

from dataclasses import dataclass

from memcore.memory_worker.prompts.contracts import SemanticUnit

from .contracts import CoverageDisposition, PersistedUnitAuthority


@dataclass(frozen=True)
class DispositionDecision:
    disposition: CoverageDisposition
    reason_codes: tuple[str, ...]


def disposition_for_unit(
    unit: SemanticUnit,
    *,
    authority: PersistedUnitAuthority | None,
    authority_count: int,
    has_unresolved_reference: bool,
    mapping_supported: bool,
) -> DispositionDecision:
    """Apply the frozen WP02 precedence without interpreting raw language."""

    if (
        authority is None
        or authority_count != 1
        or authority.conflicting_authority
        or authority.gate_decision_uuid is None
        or authority.route_uuid is None
        or authority.annotation_uuid is None
        or authority.route_type is None
        or authority.route_status is None
        or not authority.privacy_storage_allowed
        or authority.privacy_ceiling in {"sensitive", "secret"}
    ):
        reasons: list[str] = []
        if authority is None or authority_count == 0:
            reasons.append("missing_persisted_authority")
        if authority_count > 1:
            reasons.append("cross_authority_span")
        if authority is not None:
            if authority.conflicting_authority:
                reasons.append("conflicting_persisted_authority")
            if authority.privacy_ceiling != "normal" or not authority.privacy_storage_allowed:
                reasons.append("privacy_or_sensitivity_ceiling")
            if (
                authority.gate_decision_uuid is None
                or authority.route_uuid is None
                or authority.annotation_uuid is None
                or authority.route_type is None
                or authority.route_status is None
            ):
                reasons.append("incomplete_authority_lineage")
        return DispositionDecision(
            CoverageDisposition.NEEDS_REVIEW,
            tuple(dict.fromkeys(reasons or ["authority_requires_review"])),
        )
    if has_unresolved_reference:
        return DispositionDecision(
            CoverageDisposition.UNRESOLVED_REFERENCE,
            ("ambiguous_or_unresolved_reference",),
        )
    if unit.durability == "context_only":
        return DispositionDecision(CoverageDisposition.CONTEXT_ONLY, ("context_only",))
    if unit.unit_type == "instruction" and unit.durability == "transient":
        return DispositionDecision(
            CoverageDisposition.TRANSIENT_INSTRUCTION, ("transient_instruction",)
        )
    if unit.unit_type == "question" or unit.epistemic_status in {
        "hypothetical",
        "questioned",
    }:
        return DispositionDecision(CoverageDisposition.CONTEXT_ONLY, ("non_asserted_or_question",))
    if unit.durability == "unknown" or unit.epistemic_status == "unknown":
        return DispositionDecision(
            CoverageDisposition.NEEDS_REVIEW, ("unknown_durability_or_epistemic_status",)
        )
    if not mapping_supported:
        return DispositionDecision(CoverageDisposition.UNSUPPORTED, ("route_mapping_unsupported",))
    if unit.durability != "durable":
        return DispositionDecision(
            CoverageDisposition.UNSUPPORTED, ("durability_not_candidate_eligible",)
        )
    return DispositionDecision(CoverageDisposition.DURABLE_CANDIDATE, ("durable_policy_eligible",))
