"""Shared Jakobson contract execution used by the Lite and Full pipelines.

Both pipelines segment text and build a deterministic fallback differently, but
the provider contract execution (capability-correct request, strict validation,
bounded repair, deterministic fallback, audit) must be identical. That common
core lives here so Lite and Full keep semantic parity.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from memcore.memory_worker.execution import (
    ContractExecutionOutcome,
    run_contract_execution,
)
from memcore.memory_worker.prompts.contracts import (
    canonical_jakobson_v3_example,
    get_contract,
)
from memcore.memory_worker.prompts.registry import render_prompt, validate_prompt_execution
from memcore.memory_worker.prompts.validators import PromptValidationError
from memcore.memory_worker.prompts.versions import (
    JAKOBSON_SENTENCE_ANALYSIS_ACTIVE_VERSION,
    JAKOBSON_SENTENCE_ANALYSIS_PROMPT_ID,
)
from memcore.memory_worker.providers.openai_compatible import (
    OpenAICompatibleMemoryExtractionProvider,
)
from memcore.validators.ijson import dump_ijson

REMOTE_PROVIDER_TYPES = {"openai_compatible", "openai_compatible_llm"}


def execute_jakobson_contract(
    *,
    profile: dict[str, Any],
    input_payload: dict[str, Any],
    deterministic_builder: Callable[[], dict[str, Any]],
    revalidate: Callable[[], None] | None = None,
    timeout_ms: int = 8000,
) -> ContractExecutionOutcome:
    """Execute the active Jakobson contract for a message and return the outcome.

    ``deterministic_builder`` must return a contract-valid active-version output;
    it is used both when the profile is deterministic and as the fallback when a
    remote provider cannot satisfy the contract.
    """

    provider_type = str(profile.get("provider_type") or profile.get("provider") or "deterministic")
    contract = get_contract(
        JAKOBSON_SENTENCE_ANALYSIS_PROMPT_ID, JAKOBSON_SENTENCE_ANALYSIS_ACTIVE_VERSION
    )

    def validate(output: Any) -> list[dict[str, str]]:
        try:
            validate_prompt_execution(
                JAKOBSON_SENTENCE_ANALYSIS_PROMPT_ID,
                JAKOBSON_SENTENCE_ANALYSIS_ACTIVE_VERSION,
                input_payload,
                dict(output),
            )
            return []
        except PromptValidationError:
            issues = contract.validate(output) if contract is not None else []
            return issues or [
                {"path": "(root)", "code": "invalid", "message": "contract validation failed"}
            ]

    if provider_type not in REMOTE_PROVIDER_TYPES:
        return run_contract_execution(
            provider=None,
            system_prompt="",
            input_payload=input_payload,
            contract=contract,
            validate=validate,
            deterministic_output=deterministic_builder,
        )

    provider = OpenAICompatibleMemoryExtractionProvider.from_profile(profile, timeout_ms=timeout_ms)
    system_prompt = render_prompt(
        JAKOBSON_SENTENCE_ANALYSIS_PROMPT_ID,
        JAKOBSON_SENTENCE_ANALYSIS_ACTIVE_VERSION,
        input_payload,
    ).replace("{{CANONICAL_EXAMPLE_IJSON}}", dump_ijson(canonical_jakobson_v3_example()))
    return run_contract_execution(
        provider=provider,
        system_prompt=system_prompt,
        input_payload=input_payload,
        contract=contract,
        validate=validate,
        deterministic_output=deterministic_builder,
        revalidate=revalidate,
        allow_fallback=True,
    )
