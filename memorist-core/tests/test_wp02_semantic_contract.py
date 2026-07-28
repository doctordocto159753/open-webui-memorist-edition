from __future__ import annotations

import copy
import hashlib
from typing import Any

import pytest
from pydantic import ValidationError

from memcore.memory_worker.prompts.contracts import (
    JAKOBSON_V3_CONTRACT,
    SEMANTIC_CANDIDATE_V1_CONTRACT,
    SemanticAnalysisV1Output,
    canonical_semantic_candidate_v1_example,
)
from memcore.memory_worker.prompts.registry import get_prompt
from memcore.memory_worker.providers.openai_compatible import ProviderAttempt
from memcore.memory_worker.semantic_contract import (
    BoundedContextItem,
    SemanticContextBoundary,
    build_semantic_input,
    validate_semantic_binding,
)
from memcore.memory_worker.semantic_runtime import execute_semantic_candidate_contract
from memcore.model_control.role_contracts import memory_extraction_contract_bundle
from memcore.textsemantics.result import build_envelope

RAW = "Keep backups enabled."


def _input() -> dict[str, Any]:
    value = build_semantic_input(
        current_message_uuid="message-1",
        current_message_version_uuid=None,
        current_raw_text=RAW,
        text_envelope=build_envelope(RAW),
        bounded_context_items=[],
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
    return value.model_dump(mode="json")


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("extra",), "forbidden"),
        (("status",), "reject"),
        (("semantic_units", 0, "raw_start"), "0"),
        (("semantic_units", 0, "unit_type"), "fact"),
        (("semantic_units", 0, "durability"), "reported"),
        (("semantic_units", 0, "epistemic_status"), "qualifies"),
    ],
)
def test_semantic_contract_is_required_closed_and_strict(
    path: tuple[str | int, ...], value: Any
) -> None:
    output = canonical_semantic_candidate_v1_example()
    target: Any = output
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value
    assert SEMANTIC_CANDIDATE_V1_CONTRACT.validate(output)

    missing = canonical_semantic_candidate_v1_example()
    missing.pop("relations")
    with pytest.raises(ValidationError):
        SemanticAnalysisV1Output.model_validate(missing)


def test_semantic_binding_accepts_canonical_references_and_relations() -> None:
    raw = "It stays enabled."
    semantic_input = build_semantic_input(
        current_message_uuid="message-2",
        current_message_version_uuid=None,
        current_raw_text=raw,
        text_envelope=build_envelope(raw),
        bounded_context_items=[
            BoundedContextItem(
                context_item_id="context-1",
                user_uuid="user-1",
                session_uuid="session-1",
                workspace_uuid=None,
                project_uuid=None,
                message_uuid="message-1",
                message_version_uuid=None,
                text_unit_uuid="text-unit-1",
                role="assistant",
                turn_index=1,
                unit_index=0,
                raw_start=0,
                raw_end=21,
                text=RAW,
                raw_text_hash=hashlib.sha256(RAW.encode()).hexdigest(),
                source_authority_ceiling="assistant_claim",
            )
        ],
        boundary=SemanticContextBoundary(
            user_uuid="user-1",
            session_uuid="session-1",
            workspace_uuid=None,
            project_uuid=None,
            baseline_limit=2,
            effective_limit=2,
            dependency_expansion=False,
        ),
    ).model_dump(mode="json")
    output = canonical_semantic_candidate_v1_example()
    output["semantic_units"][0].update(
        raw_end=len(raw), evidence=raw, proposition="Backups stay enabled."
    )
    output["references"] = [
        {
            "id": "reference-1",
            "source_unit_id": "unit-1",
            "marker_start": 0,
            "marker_end": 2,
            "marker_evidence": "It",
            "status": "resolved",
            "candidate_referent_ids": ["prior_context:context-1"],
            "selected_referent_id": "prior_context:context-1",
        }
    ]
    output["relations"] = [
        {
            "id": "relation-1",
            "relation_type": "ratifies",
            "source_unit_id": "unit-1",
            "target_referent_id": "prior_context:context-1",
            "evidence_start": 0,
            "evidence_end": len(raw),
            "evidence": raw,
        }
    ]
    report = validate_semantic_binding(semantic_input, output)
    assert report.accepted_reference_indexes == (0,)
    assert report.accepted_relation_indexes == (0,)

    outside = copy.deepcopy(output)
    outside["references"][0]["candidate_referent_ids"] = ["prior_context:not-supplied"]
    with pytest.raises(ValueError, match="outside the supplied manifest"):
        validate_semantic_binding(semantic_input, outside)


def test_abstention_and_evidence_fail_closed() -> None:
    output = canonical_semantic_candidate_v1_example()
    output["status"] = "abstain"
    with pytest.raises(ValueError, match="empty semantic collections"):
        validate_semantic_binding(_input(), output)
    output = canonical_semantic_candidate_v1_example()
    output["semantic_units"][0]["evidence"] = "tidied"
    with pytest.raises(ValueError, match="evidence_not_a_slice"):
        validate_semantic_binding(_input(), output)


class _SequenceProvider:
    capability_mode = "json_object"

    def __init__(self, outputs: list[dict[str, Any]]) -> None:
        self.outputs = outputs
        self.calls = 0
        self.system_prompt = ""

    def run(self, *, system_prompt: str, **_: Any) -> ProviderAttempt:
        self.system_prompt = system_prompt
        output = self.outputs[min(self.calls, len(self.outputs) - 1)]
        self.calls += 1
        return ProviderAttempt(output, None, 1, 1, 1, f"response-{self.calls}", 200)


@pytest.mark.parametrize(("invalid_first", "expected_calls"), [(False, 1), (True, 2)])
def test_semantic_runtime_valid_first_or_one_repair(
    monkeypatch: pytest.MonkeyPatch, invalid_first: bool, expected_calls: int
) -> None:
    valid = canonical_semantic_candidate_v1_example()
    invalid = dict(valid, status="reject")
    provider = _SequenceProvider([invalid, valid] if invalid_first else [valid])
    monkeypatch.setattr(
        "memcore.memory_worker.semantic_runtime."
        "OpenAICompatibleMemoryExtractionProvider.from_profile",
        lambda *_args, **_kwargs: provider,
    )
    outcome = execute_semantic_candidate_contract(
        profile={
            "provider_type": "openai_compatible_llm",
            "endpoint_url": "http://unused",
            "model_name": "controlled",
            "supports_json_mode": True,
        },
        input_payload=_input(),
    )
    assert outcome.fallback_used is False
    assert outcome.repair_attempted is invalid_first
    assert provider.calls == expected_calls
    assert "top-level fields required by the supplied strict schema" in provider.system_prompt
    assert '"semantic_units"' in provider.system_prompt
    assert '"additionalProperties":false' in provider.system_prompt
    assert "{{" not in provider.system_prompt


def test_semantic_runtime_invalid_twice_returns_empty_abstention(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invalid = dict(canonical_semantic_candidate_v1_example(), status="reject")
    provider = _SequenceProvider([invalid, invalid])
    monkeypatch.setattr(
        "memcore.memory_worker.semantic_runtime."
        "OpenAICompatibleMemoryExtractionProvider.from_profile",
        lambda *_args, **_kwargs: provider,
    )
    outcome = execute_semantic_candidate_contract(
        profile={
            "provider_type": "openai_compatible_llm",
            "endpoint_url": "http://unused",
            "model_name": "controlled",
        },
        input_payload=_input(),
    )
    assert outcome.fallback_used is True
    assert outcome.output["status"] == "abstain"
    assert outcome.output["semantic_units"] == []
    assert provider.calls == 2


def test_memory_extraction_bundle_orders_both_contracts_without_changing_jakobson() -> None:
    bundle = memory_extraction_contract_bundle()
    assert bundle["bundle_id"] == "memory-extraction-contract-bundle-v1"
    assert [item["typed_contract_hash"] for item in bundle["prompts"]] == [
        JAKOBSON_V3_CONTRACT.contract_hash,
        SEMANTIC_CANDIDATE_V1_CONTRACT.contract_hash,
    ]
    assert (
        JAKOBSON_V3_CONTRACT.contract_hash
        == "279c5809d7270717aba91b2f90e80590256a0d5c236b664a620688f2dc1078eb"
    )
    assert get_prompt("memorist.semantic_candidate_analysis", "1.0").model_role.value == (
        "memory_extraction"
    )
