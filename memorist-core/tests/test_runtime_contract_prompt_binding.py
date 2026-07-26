from __future__ import annotations

import pytest

from memcore.model_control.runtime_contracts import runtime_contract_for_role
from memcore.model_control.stage_invocation import StageInvocationRequest, _stage_prompt
from memcore.models import ModelRole


@pytest.mark.parametrize(
    "role",
    [
        ModelRole.HIGH_CONFIDENCE_EXTRACTION,
        ModelRole.PRIVACY_SENSITIVITY,
        ModelRole.BLOCK_COMPACTION,
    ],
)
def test_certification_probe_prompt_is_exact_stage_invoker_prompt(role: ModelRole) -> None:
    contract = runtime_contract_for_role(role)
    assert contract is not None
    request = StageInvocationRequest(
        role=role,
        stage=contract.stage,
        source_type="certification_probe",
        source_uuid=f"certification:{role.value}",
        prompt_version=contract.runtime_prompt_version,
        input_payload=contract.certification_input,
    )
    assert contract.render_probe_prompt() == _stage_prompt(request)


def test_runtime_contract_rejects_type_coercion_and_extra_fields() -> None:
    contract = runtime_contract_for_role(ModelRole.HIGH_CONFIDENCE_EXTRACTION)
    assert contract is not None
    invalid = {
        "decision": "approved",
        "confidence": "0.9",
        "reason_codes": ["evidence_alignment"],
        "evidence_spans": [],
        "unexpected": True,
    }
    with pytest.raises(ValueError):
        contract.validate(invalid)
