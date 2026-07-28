"""Pure deterministic coverage planner; no I/O and no semantic guessing."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from typing import Any, Literal

from memcore.memory_worker.semantic.candidate_mapping import candidate_mapping_for_route
from memcore.memory_worker.semantic.provenance_policy import decide_candidate_provenance

from .contracts import (
    COVERAGE_PLAN_VERSION,
    COVERAGE_PLANNER_VERSION,
    CandidateProposal,
    CoverageDisposition,
    CoverageItem,
    CoveragePlan,
    CoveragePlannerInput,
    PersistedUnitAuthority,
)
from .identity import (
    coverage_item_identity,
    coverage_plan_hash,
    proposal_identity,
    semantic_unit_fingerprint,
)
from .policy import disposition_for_unit


def plan_candidate_coverage(
    planner_input: CoveragePlannerInput,
) -> tuple[CoveragePlan, tuple[CandidateProposal, ...]]:
    """Assign complete coverage and at most one proposal per accepted unit."""

    envelope_hash = str(planner_input.text_envelope.get("raw_text_hash") or "")
    actual_hash = hashlib.sha256(planner_input.current_raw_text.encode("utf-8")).hexdigest()
    if envelope_hash != actual_hash:
        raise ValueError("TextEnvelope does not bind the current raw message")

    analysis = planner_input.semantic_analysis
    accepted = set(planner_input.accepted_unit_ids)
    units = [unit for unit in analysis.semantic_units if unit.id in accepted]
    if len(units) != len(accepted):
        raise ValueError("accepted unit IDs must name semantic output units")
    proposals: list[CandidateProposal] = []
    items: list[CoverageItem] = []
    for unit in units:
        containing = _containing_authorities(
            planner_input.authorities, unit.raw_start, unit.raw_end
        )
        authority = containing[0] if len(containing) == 1 else None
        if authority is not None and (
            planner_input.semantic_prompt_execution_uuid is None
            or _assistant_reference_is_not_ratified(planner_input, unit.id)
        ):
            authority = authority.model_copy(update={"conflicting_authority": True})
        mapping = (
            candidate_mapping_for_route(
                authority.route_type,
                unit.proposition,
                message_uuid=planner_input.message_uuid,
            )
            if authority is not None
            else None
        )
        unresolved = any(
            analysis.references[index].source_unit_id == unit.id
            and analysis.references[index].status != "resolved"
            for index in planner_input.accepted_reference_indexes
        )
        decision = disposition_for_unit(
            unit,
            authority=authority,
            authority_count=len(containing),
            has_unresolved_reference=unresolved,
            mapping_supported=mapping is not None,
        )
        fingerprint = semantic_unit_fingerprint(
            unit=unit,
            analysis=analysis,
            accepted_reference_indexes=planner_input.accepted_reference_indexes,
            accepted_relation_indexes=planner_input.accepted_relation_indexes,
            context_items=planner_input.bounded_context_items,
        )
        proposal: CandidateProposal | None = None
        reasons = list(decision.reason_codes)
        if decision.disposition is CoverageDisposition.DURABLE_CANDIDATE:
            assert authority is not None and mapping is not None
            provenance = decide_candidate_provenance(
                message_role=planner_input.message_role,
                route_type=mapping.route_type,
                route_status=mapping.status,
                imported_record=planner_input.imported_record,
            )
            if (
                not provenance.allows_candidate_creation
                or not provenance.allows_automatic_memory_creation
                or mapping.status.value != "ready_for_consolidation"
            ):
                decision = disposition_for_unit(
                    unit,
                    authority=authority.model_copy(update={"conflicting_authority": True}),
                    authority_count=1,
                    has_unresolved_reference=False,
                    mapping_supported=True,
                )
                reasons = [*decision.reason_codes, *provenance.reason_codes]
            else:
                proposal_id, _identity_hash = proposal_identity(
                    planner_version=COVERAGE_PLANNER_VERSION,
                    message_uuid=planner_input.message_uuid,
                    raw_text_hash=actual_hash,
                    semantic_contract_hash=planner_input.semantic_contract_hash,
                    unit_fingerprint=fingerprint,
                    raw_start=unit.raw_start,
                    raw_end=unit.raw_end,
                    route_type=str(authority.route_type),
                    route_status=str(authority.route_status),
                    gate_decision=str(authority.gate_decision),
                    source_authority=provenance.source_authority.value,
                    coverage_disposition=CoverageDisposition.DURABLE_CANDIDATE.value,
                )
                proposal = CandidateProposal(
                    proposal_id=proposal_id,
                    semantic_unit_id=unit.id,
                    message_uuid=planner_input.message_uuid,
                    text_unit_uuid=authority.text_unit_uuid,
                    raw_start=unit.raw_start,
                    raw_end=unit.raw_end,
                    evidence=unit.evidence,
                    candidate_type=mapping.candidate_type.value,
                    subject_key=mapping.subject_key,
                    predicate=mapping.predicate,
                    object_payload={"value": mapping.object_value},
                    normalized_text=mapping.normalized_text,
                    polarity=unit.polarity,
                    epistemic_status=unit.epistemic_status,
                    durability=unit.durability,
                    source_authority=provenance.source_authority.value,
                    explicitness=provenance.explicitness.value,
                    privacy_ceiling=authority.privacy_ceiling,
                    status=provenance.status.value,
                    gate_decision_uuid=str(authority.gate_decision_uuid),
                    route_uuid=str(authority.route_uuid),
                    annotation_uuid=str(authority.annotation_uuid),
                    prompt_execution_uuid=str(planner_input.semantic_prompt_execution_uuid),
                    context_lineage=_context_lineage(planner_input, unit.id),
                    reason_codes=tuple(dict.fromkeys([*reasons, *provenance.reason_codes])),
                    automatic_candidate_creation_allowed=True,
                    semantic_unit_fingerprint=fingerprint,
                )
                proposals.append(proposal)
        linked_proposal_id: str | None = proposal.proposal_id if proposal is not None else None
        items.append(
            _coverage_item(
                planner_input,
                semantic_unit_id=unit.id,
                fingerprint=fingerprint,
                raw_start=unit.raw_start,
                raw_end=unit.raw_end,
                disposition=decision.disposition,
                reason_codes=tuple(dict.fromkeys(reasons)),
                authority=authority,
                proposal_id=linked_proposal_id,
            )
        )

    items.extend(_uncovered_items(planner_input, units))
    items.sort(key=lambda item: (item.raw_start, item.raw_end, item.semantic_unit_id or ""))
    _assert_complete(planner_input, items, proposals)
    status = _plan_status(planner_input, items)
    warnings = (
        ("semantic_model_abstained",) if planner_input.semantic_analysis.status == "abstain" else ()
    )
    without_hash: dict[str, Any] = {
        "coverage_plan_version": COVERAGE_PLAN_VERSION,
        "message_uuid": planner_input.message_uuid,
        "raw_text_hash": actual_hash,
        "processing_run_uuid": planner_input.processing_run_uuid,
        "semantic_prompt_execution_uuid": planner_input.semantic_prompt_execution_uuid,
        "semantic_contract_hash": planner_input.semantic_contract_hash,
        "status": status,
        "items": [item.model_dump(mode="json") for item in items],
        "warnings": list(warnings),
    }
    plan = CoveragePlan(
        coverage_plan_version=COVERAGE_PLAN_VERSION,
        message_uuid=planner_input.message_uuid,
        raw_text_hash=actual_hash,
        processing_run_uuid=planner_input.processing_run_uuid,
        semantic_prompt_execution_uuid=planner_input.semantic_prompt_execution_uuid,
        semantic_contract_hash=planner_input.semantic_contract_hash,
        status=status,
        items=tuple(items),
        warnings=warnings,
        coverage_hash=coverage_plan_hash(without_hash),
    )
    return plan, tuple(proposals)


def _coverage_item(
    value: CoveragePlannerInput,
    *,
    semantic_unit_id: str | None,
    fingerprint: str | None,
    raw_start: int,
    raw_end: int,
    disposition: CoverageDisposition,
    reason_codes: tuple[str, ...],
    authority: PersistedUnitAuthority | None,
    proposal_id: str | None,
) -> CoverageItem:
    item_id = coverage_item_identity(
        {
            "planner_version": COVERAGE_PLANNER_VERSION,
            "message_uuid": value.message_uuid,
            "raw_text_hash": value.text_envelope["raw_text_hash"],
            "semantic_contract_hash": value.semantic_contract_hash,
            "semantic_unit_fingerprint": fingerprint,
            "raw_start": raw_start,
            "raw_end": raw_end,
            "disposition": disposition.value,
            "reason_codes": list(reason_codes),
            "proposal_id": proposal_id,
        }
    )
    return CoverageItem(
        coverage_item_id=item_id,
        semantic_unit_id=semantic_unit_id,
        raw_start=raw_start,
        raw_end=raw_end,
        disposition=disposition,
        reason_codes=reason_codes,
        gate_decision_uuid=authority.gate_decision_uuid if authority else None,
        route_uuid=authority.route_uuid if authority else None,
        proposal_id=proposal_id,
    )


def _uncovered_items(value: CoveragePlannerInput, units: Sequence[Any]) -> list[CoverageItem]:
    covered = [(unit.raw_start, unit.raw_end) for unit in units]
    tokens = [
        token
        for token in value.text_envelope.get("tokens", [])
        if not any(
            int(token["raw_start"]) < end and start < int(token["raw_end"])
            for start, end in covered
        )
    ]
    groups: list[list[dict[str, Any]]] = []
    group_authority_keys: list[str | None] = []
    for token in tokens:
        token_start = int(token["raw_start"])
        token_end = int(token["raw_end"])
        token_authorities = _containing_authorities(
            value.authorities,
            token_start,
            token_end,
        )
        authority_key = token_authorities[0].text_unit_uuid if len(token_authorities) == 1 else None
        if not groups:
            groups.append([token])
            group_authority_keys.append(authority_key)
            continue
        previous = groups[-1][-1]
        gap = value.current_raw_text[int(previous["raw_end"]) : int(token["raw_start"])]
        if authority_key == group_authority_keys[-1] and not any(
            character.isalnum() for character in gap
        ):
            groups[-1].append(token)
        else:
            groups.append([token])
            group_authority_keys.append(authority_key)

    items: list[CoverageItem] = []
    for group in groups:
        start, end = int(group[0]["raw_start"]), int(group[-1]["raw_end"])
        authorities = _containing_authorities(value.authorities, start, end)
        authority = authorities[0] if len(authorities) == 1 else None
        rejected = authority is not None and authority.gate_decision in {
            "discard",
            "retain_raw_only",
        }
        disposition = (
            CoverageDisposition.REJECTED_BY_GATE if rejected else CoverageDisposition.UNSUPPORTED
        )
        reasons = (
            (f"gate_{authority.gate_decision}",)
            if rejected and authority is not None
            else ("uncovered_material",)
        )
        items.append(
            _coverage_item(
                value,
                semantic_unit_id=None,
                fingerprint=None,
                raw_start=start,
                raw_end=end,
                disposition=disposition,
                reason_codes=reasons,
                authority=authority,
                proposal_id=None,
            )
        )
    return items


def _containing_authorities(
    authorities: Sequence[PersistedUnitAuthority], start: int, end: int
) -> list[PersistedUnitAuthority]:
    return [
        authority
        for authority in authorities
        if authority.raw_start <= start and end <= authority.raw_end
    ]


def _context_lineage(
    value: CoveragePlannerInput, semantic_unit_id: str
) -> tuple[dict[str, Any], ...]:
    contexts = {item.context_item_id: item for item in value.bounded_context_items}
    lineage: list[dict[str, Any]] = []
    for index in value.accepted_reference_indexes:
        reference = value.semantic_analysis.references[index]
        if (
            reference.source_unit_id != semantic_unit_id
            or reference.selected_referent_id is None
            or not reference.selected_referent_id.startswith("prior_context:")
        ):
            continue
        item = contexts.get(reference.selected_referent_id.removeprefix("prior_context:"))
        if item is None:
            continue
        lineage.append(
            {
                "context_item_id": item.context_item_id,
                "message_uuid": item.message_uuid,
                "message_version_uuid": item.message_version_uuid,
                "text_unit_uuid": item.text_unit_uuid,
                "role": item.role,
                "raw_start": item.raw_start,
                "raw_end": item.raw_end,
                "raw_text_hash": item.raw_text_hash,
                "source_authority_ceiling": item.source_authority_ceiling,
                "reference_marker_start": reference.marker_start,
                "reference_marker_end": reference.marker_end,
                "semantic_contract_hash": value.semantic_contract_hash,
            }
        )
    return tuple(lineage)


def _assistant_reference_is_not_ratified(
    value: CoveragePlannerInput, semantic_unit_id: str
) -> bool:
    contexts = {item.context_item_id: item for item in value.bounded_context_items}
    targets: set[str] = set()
    for index in value.accepted_reference_indexes:
        reference = value.semantic_analysis.references[index]
        if (
            reference.source_unit_id != semantic_unit_id
            or reference.status != "resolved"
            or reference.selected_referent_id is None
            or not reference.selected_referent_id.startswith("prior_context:")
        ):
            continue
        item = contexts.get(reference.selected_referent_id.removeprefix("prior_context:"))
        if item is not None and item.role == "assistant":
            targets.add(reference.selected_referent_id)
    if not targets:
        return False
    if value.message_role != "user" or len(targets) != 1:
        return True
    return not any(
        value.semantic_analysis.relations[index].source_unit_id == semantic_unit_id
        and value.semantic_analysis.relations[index].relation_type in {"ratifies", "corrects"}
        and value.semantic_analysis.relations[index].target_referent_id in targets
        for index in value.accepted_relation_indexes
    )


def _assert_complete(
    value: CoveragePlannerInput,
    items: Sequence[CoverageItem],
    proposals: Sequence[CandidateProposal],
) -> None:
    unit_items = [item for item in items if item.semantic_unit_id is not None]
    if len(unit_items) != len(value.accepted_unit_ids):
        raise ValueError("coverage must contain exactly one item per accepted unit")
    if len({item.semantic_unit_id for item in unit_items}) != len(unit_items):
        raise ValueError("coverage contains duplicate semantic units")
    if len({item.coverage_item_id for item in items}) != len(items):
        raise ValueError("coverage item identity collision")
    proposal_ids = {proposal.proposal_id for proposal in proposals}
    durable_ids = {
        item.proposal_id
        for item in items
        if item.disposition is CoverageDisposition.DURABLE_CANDIDATE
    }
    if None in durable_ids or durable_ids != proposal_ids:
        raise ValueError("durable coverage and proposals are inconsistent")
    if any(
        item.proposal_id is not None
        and item.disposition is not CoverageDisposition.DURABLE_CANDIDATE
        for item in items
    ):
        raise ValueError("only durable coverage may name a proposal")


def _plan_status(
    value: CoveragePlannerInput, items: Sequence[CoverageItem]
) -> Literal["complete", "abstain", "retain_raw_only", "needs_review"]:
    if value.semantic_analysis.status == "abstain":
        return "abstain"
    dispositions = {item.disposition for item in items}
    if CoverageDisposition.NEEDS_REVIEW in dispositions:
        return "needs_review"
    if dispositions and dispositions <= {CoverageDisposition.REJECTED_BY_GATE}:
        return "retain_raw_only"
    return "complete"
