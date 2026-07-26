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
