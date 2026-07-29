"""Canonical I-JSON fingerprints and UUIDv5 identities for WP02 coverage."""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Mapping, Sequence
from typing import Any

from memcore.memory_worker.prompts.contracts import SemanticAnalysisV1Output, SemanticUnit
from memcore.memory_worker.semantic_contract import BoundedContextItem
from memcore.validators.ijson import canonical_hash_ijson

PROPOSAL_IDENTITY_VERSION = "memorist.semantic_candidate.proposal_identity.v1"
COVERAGE_ITEM_IDENTITY_VERSION = "memorist.semantic_candidate.coverage_item_identity.v1"
PROPOSAL_NAMESPACE = uuid.NAMESPACE_URL
PROPOSAL_NAME_PREFIX = "memorist:semantic-candidate-proposal:v1:"
COVERAGE_ITEM_NAMESPACE = uuid.UUID("0e4f3e9a-c687-53a0-a948-bc544393f77d")


def semantic_unit_fingerprint(
    *,
    unit: SemanticUnit,
    analysis: SemanticAnalysisV1Output,
    accepted_reference_indexes: Sequence[int],
    accepted_relation_indexes: Sequence[int],
    context_items: Sequence[BoundedContextItem],
) -> str:
    contexts = {item.context_item_id: item for item in context_items}
    units = {item.id: item for item in analysis.semantic_units}
    references: list[dict[str, Any]] = []
    for index in accepted_reference_indexes:
        reference = analysis.references[index]
        if reference.source_unit_id != unit.id:
            continue
        references.append(
            {
                "marker_start": reference.marker_start,
                "marker_end": reference.marker_end,
                "marker_hash": _text_hash(reference.marker_evidence),
                "status": reference.status,
                "selected_target": _target_fingerprint(
                    reference.selected_referent_id, units, contexts
                ),
            }
        )
    relations: list[dict[str, Any]] = []
    for index in accepted_relation_indexes:
        relation = analysis.relations[index]
        if relation.source_unit_id != unit.id:
            continue
        relations.append(
            {
                "relation_type": relation.relation_type,
                "evidence_start": relation.evidence_start,
                "evidence_end": relation.evidence_end,
                "evidence_hash": _text_hash(relation.evidence),
                "target": _target_fingerprint(relation.target_referent_id, units, contexts),
            }
        )
    material = {
        "raw_start": unit.raw_start,
        "raw_end": unit.raw_end,
        "evidence_hash": _text_hash(unit.evidence),
        "proposition": unit.proposition,
        "unit_type": unit.unit_type,
        "durability": unit.durability,
        "polarity": unit.polarity,
        "epistemic_status": unit.epistemic_status,
        "references": sorted(references, key=canonical_hash_ijson),
        "relations": sorted(relations, key=canonical_hash_ijson),
    }
    return canonical_hash_ijson(material)


def proposal_identity(
    *,
    planner_version: str,
    message_uuid: str,
    raw_text_hash: str,
    semantic_contract_hash: str,
    unit_fingerprint: str,
    raw_start: int,
    raw_end: int,
    route_type: str,
    route_status: str,
    gate_decision: str,
    source_authority: str,
    coverage_disposition: str,
) -> tuple[str, str]:
    material = {
        "identity_version": PROPOSAL_IDENTITY_VERSION,
        "planner_version": planner_version,
        "message_uuid": message_uuid,
        "raw_text_hash": raw_text_hash,
        "semantic_contract_hash": semantic_contract_hash,
        "semantic_unit_fingerprint": unit_fingerprint,
        "raw_start": raw_start,
        "raw_end": raw_end,
        "route_type": route_type,
        "route_status": route_status,
        "gate_decision": gate_decision,
        "source_authority": source_authority,
        "coverage_disposition": coverage_disposition,
    }
    digest = canonical_hash_ijson(material)
    return str(uuid.uuid5(PROPOSAL_NAMESPACE, f"{PROPOSAL_NAME_PREFIX}{digest}")), digest


def coverage_item_identity(material: Mapping[str, Any]) -> str:
    digest = canonical_hash_ijson(
        {"identity_version": COVERAGE_ITEM_IDENTITY_VERSION, **dict(material)}
    )
    return str(uuid.uuid5(COVERAGE_ITEM_NAMESPACE, digest))


def coverage_plan_hash(plan_without_hash: Mapping[str, Any]) -> str:
    return canonical_hash_ijson(plan_without_hash)


def _target_fingerprint(
    referent_id: str | None,
    units: Mapping[str, SemanticUnit],
    contexts: Mapping[str, BoundedContextItem],
) -> dict[str, Any] | None:
    if referent_id is None:
        return None
    if referent_id.startswith("current_unit:"):
        unit = units.get(referent_id.removeprefix("current_unit:"))
        if unit is None:
            return {"kind": "invalid"}
        return {
            "kind": "current_unit",
            "fingerprint": canonical_hash_ijson(
                {
                    "raw_start": unit.raw_start,
                    "raw_end": unit.raw_end,
                    "evidence_hash": _text_hash(unit.evidence),
                    "proposition": unit.proposition,
                    "unit_type": unit.unit_type,
                    "durability": unit.durability,
                    "polarity": unit.polarity,
                    "epistemic_status": unit.epistemic_status,
                }
            ),
        }
    if referent_id.startswith("prior_context:"):
        item = contexts.get(referent_id.removeprefix("prior_context:"))
        if item is None:
            return {"kind": "invalid"}
        return {
            "kind": "prior_context",
            "context_item_id": item.context_item_id,
            "message_uuid": item.message_uuid,
            "message_version_uuid": item.message_version_uuid,
            "text_unit_uuid": item.text_unit_uuid,
            "raw_start": item.raw_start,
            "raw_end": item.raw_end,
            "raw_text_hash": item.raw_text_hash,
            "role": item.role,
            "source_authority_ceiling": item.source_authority_ceiling,
        }
    return {"kind": "invalid"}


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
