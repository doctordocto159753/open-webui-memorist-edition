"""Shared one-repair runtime for the frozen WP02 semantic contract."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace
from typing import Any

from memcore.memory_worker.attempt_audit import ProviderAttemptAuditRepository
from memcore.memory_worker.execution import ContractExecutionOutcome, run_contract_execution
from memcore.memory_worker.extraction.sensitivity import classify_sensitivity
from memcore.memory_worker.jakobson_runtime import REMOTE_PROVIDER_TYPES
from memcore.memory_worker.prompts.contracts import (
    SEMANTIC_CANDIDATE_V1_CONTRACT,
    canonical_semantic_candidate_v1_example,
)
from memcore.memory_worker.prompts.registry import render_prompt, validate_prompt_execution
from memcore.memory_worker.prompts.validators import PromptValidationError
from memcore.memory_worker.prompts.versions import (
    SEMANTIC_CANDIDATE_ANALYSIS_PROMPT_ID,
    SEMANTIC_CANDIDATE_ANALYSIS_VERSION,
)
from memcore.memory_worker.providers.openai_compatible import (
    OpenAICompatibleMemoryExtractionProvider,
)
from memcore.memory_worker.semantic_contract import SemanticAnalysisV1Input
from memcore.models import SensitivityClass
from memcore.validators.ijson import dump_ijson


def semantic_abstention(reason_code: str = "semantic_analysis_unavailable") -> dict[str, Any]:
    """Return the content-free, contract-valid fail-closed output."""

    safe_reason = "".join(
        character
        for character in reason_code.lower().replace(" ", "_")
        if character.isalnum() or character in {"_", "-"}
    )[:80]
    return {
        "schema_version": "1.0",
        "prompt_id": SEMANTIC_CANDIDATE_ANALYSIS_PROMPT_ID,
        "prompt_version": SEMANTIC_CANDIDATE_ANALYSIS_VERSION,
        "status": "abstain",
        "warnings": [safe_reason or "semantic_analysis_unavailable"],
        "semantic_units": [],
        "references": [],
        "relations": [],
    }


def execute_semantic_candidate_contract(
    *,
    profile: dict[str, Any],
    input_payload: SemanticAnalysisV1Input | dict[str, Any],
    revalidate: Callable[[], None] | None = None,
    timeout_ms: int = 8000,
    allow_fallback: bool = True,
    attempt_audit: ProviderAttemptAuditRepository | None = None,
) -> ContractExecutionOutcome:
    """Run strict structure, binding and WP01 evidence under one repair budget."""

    payload = (
        input_payload.model_dump(mode="json")
        if isinstance(input_payload, SemanticAnalysisV1Input)
        else SemanticAnalysisV1Input.model_validate(input_payload).model_dump(mode="json")
    )
    provider_type = str(profile.get("provider_type") or profile.get("provider") or "deterministic")

    def validate(output: Any) -> list[dict[str, str]]:
        if isinstance(output, Mapping):
            try:
                validate_prompt_execution(
                    SEMANTIC_CANDIDATE_ANALYSIS_PROMPT_ID,
                    SEMANTIC_CANDIDATE_ANALYSIS_VERSION,
                    payload,
                    dict(output),
                )
                return []
            except PromptValidationError as error:
                return [{"path": "(semantic)", "code": "invalid", "message": str(error)}]
        return SEMANTIC_CANDIDATE_V1_CONTRACT.validate(output) or [
            {"path": "(root)", "code": "invalid", "message": "contract validation failed"}
        ]

    if provider_type not in REMOTE_PROVIDER_TYPES:
        return _with_content_free_warnings(
            run_contract_execution(
                provider=None,
                system_prompt="",
                input_payload=payload,
                contract=SEMANTIC_CANDIDATE_V1_CONTRACT,
                validate=validate,
                deterministic_output=semantic_abstention,
                revalidate=revalidate,
                allow_fallback=allow_fallback,
                attempt_audit=attempt_audit,
            )
        )

    provider = OpenAICompatibleMemoryExtractionProvider.from_profile(profile, timeout_ms=timeout_ms)
    system_prompt = render_prompt(
        SEMANTIC_CANDIDATE_ANALYSIS_PROMPT_ID,
        SEMANTIC_CANDIDATE_ANALYSIS_VERSION,
        {
            # Replace trusted template material before untrusted payload data.
            # A message containing a literal ``{{STRICT_JSON_SCHEMA_IJSON}}``
            # must remain data rather than triggering a second replacement.
            "STRICT_JSON_SCHEMA_IJSON": dump_ijson(SEMANTIC_CANDIDATE_V1_CONTRACT.json_schema()),
            "CANONICAL_EXAMPLE_IJSON": dump_ijson(canonical_semantic_candidate_v1_example()),
            "PAYLOAD_IJSON": payload,
        },
    )
    return _with_content_free_warnings(
        run_contract_execution(
            provider=provider,
            system_prompt=system_prompt,
            input_payload=payload,
            contract=SEMANTIC_CANDIDATE_V1_CONTRACT,
            validate=validate,
            deterministic_output=semantic_abstention,
            revalidate=revalidate,
            allow_fallback=allow_fallback,
            attempt_audit=attempt_audit,
        )
    )


def _with_content_free_warnings(
    outcome: ContractExecutionOutcome,
) -> ContractExecutionOutcome:
    """Keep non-authoritative model warnings useful without auditing their text."""

    warnings: list[str] = []
    for index, value in enumerate(outcome.output.get("warnings") or []):
        raw = str(value)
        code = raw.lower()
        safe_code = (
            0 < len(code) <= 80
            and all(character.isalnum() or character in {"_", "-"} for character in code)
            and classify_sensitivity(code) is SensitivityClass.NORMAL
        )
        warning = code if safe_code else f"provider_warning_{index + 1}"
        if warning not in warnings:
            warnings.append(warning)
    if warnings == outcome.output.get("warnings"):
        return outcome
    return replace(outcome, output={**outcome.output, "warnings": warnings})
