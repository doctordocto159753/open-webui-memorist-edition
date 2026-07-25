"""Deterministic runtime state machine for contract-bound provider execution.

This is the single place that turns "call provider then validate" into the
bounded, auditable sequence required by the prompt-contract closure:

    provider call
    -> parse
    -> strict schema validation
    -> narrow lossless canonicalization (documented status aliases only)
    -> exactly one bounded corrective repair (when eligible)
    -> deterministic fallback (when the role allows it)

It never raises for a merely schema-invalid provider response; instead it falls
back to the deterministic output and reports the truth via
:class:`ContractExecutionOutcome`. Lease / source / profile invalidation raised
by ``revalidate`` is propagated unchanged (it must not be masked as a fallback).
"""

from __future__ import annotations

import copy
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from memcore.memory_worker.prompts.contracts import PromptContract
from memcore.memory_worker.providers.openai_compatible import (
    OpenAICompatibleMemoryExtractionProvider,
    OpenAICompatibleProviderError,
)

# Documented, lossless status aliases. Applied only when replacing the status
# makes the entire output schema-valid and nothing else changes.
STATUS_ALIASES = {"success": "ok", "ok": "ok", "done": "ok", "complete": "ok", "completed": "ok"}

Validator = Callable[[Mapping[str, Any]], list[dict[str, str]]]


@dataclass
class ContractExecutionOutcome:
    output: dict[str, Any]
    status: str
    called_provider: bool
    provider_output_valid: bool
    canonicalized: bool
    repair_attempted: bool
    repair_succeeded: bool
    fallback_used: bool
    fallback_reason: str | None
    capability_mode: str
    provider_response_id: str | None
    input_tokens: int
    output_tokens: int
    latency_ms: int
    parse_status: str
    validation_error_paths: list[str] = field(default_factory=list)


class NoDeterministicFallbackError(RuntimeError):
    """Raised when a role has no safe deterministic fallback and remote failed."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def run_contract_execution(
    *,
    provider: OpenAICompatibleMemoryExtractionProvider | None,
    system_prompt: str,
    input_payload: dict[str, Any],
    contract: PromptContract | None,
    validate: Validator,
    deterministic_output: Callable[[], dict[str, Any]],
    revalidate: Callable[[], None] | None = None,
    allow_fallback: bool = True,
) -> ContractExecutionOutcome:
    if provider is None:
        # Configured deterministic profile: not a fallback, the intended behavior.
        output = deterministic_output()
        return ContractExecutionOutcome(
            output=output,
            status=str(output.get("status") or "ok"),
            called_provider=False,
            provider_output_valid=False,
            canonicalized=False,
            repair_attempted=False,
            repair_succeeded=False,
            fallback_used=False,
            fallback_reason=None,
            capability_mode="deterministic",
            provider_response_id=None,
            input_tokens=0,
            output_tokens=len(output.get("items") or output.get("sentences") or []),
            latency_ms=0,
            parse_status="not_called",
            validation_error_paths=[],
        )

    schema = contract.json_schema() if contract is not None else None
    schema_name = (
        contract.json_schema_name if contract is not None else "memorist_structured_output"
    )
    capability_mode = provider.capability_mode

    provider_response_id: str | None = None
    input_tokens = 0
    output_tokens = 0
    latency_ms = 0
    parse_status = "parse_failed"
    last_issues: list[dict[str, str]] = []

    # ---- Attempt 1 -------------------------------------------------------
    try:
        attempt = provider.run(
            system_prompt=system_prompt,
            input_payload=input_payload,
            schema=schema,
            schema_name=schema_name,
        )
    except OpenAICompatibleProviderError as error:
        return _fallback(
            deterministic_output,
            allow_fallback=allow_fallback,
            reason=f"provider_{error.category}",
            called_provider=True,
            capability_mode=capability_mode,
            repair_attempted=False,
            repair_succeeded=False,
            parse_status="error",
            provider_response_id=None,
            input_tokens=0,
            output_tokens=0,
            latency_ms=0,
            validation_error_paths=[],
        )

    provider_response_id = attempt.provider_response_id
    input_tokens = attempt.input_tokens
    output_tokens = attempt.output_tokens
    latency_ms = attempt.latency_ms

    if attempt.parsed is not None:
        parse_status = "parsed"
        issues = validate(attempt.parsed)
        if not issues:
            return _valid_provider_outcome(
                attempt.parsed,
                capability_mode,
                provider_response_id,
                input_tokens,
                output_tokens,
                latency_ms,
                canonicalized=False,
                repair_attempted=False,
                repair_succeeded=False,
            )
        canonical = _canonicalize(attempt.parsed, validate)
        if canonical is not None:
            return _valid_provider_outcome(
                canonical,
                capability_mode,
                provider_response_id,
                input_tokens,
                output_tokens,
                latency_ms,
                canonicalized=True,
                repair_attempted=False,
                repair_succeeded=False,
            )
        last_issues = issues

    # ---- Bounded repair (exactly one) -----------------------------------
    # Re-check lease / source / profile identity BEFORE the repair call. A
    # raised invalidation must propagate; it is never masked as a fallback.
    if revalidate is not None:
        revalidate()

    corrective = {
        "invalid_output": attempt.parsed if attempt.parsed is not None else {},
        "issues": last_issues
        or [{"path": "(root)", "code": "parse", "message": attempt.parse_error or "invalid JSON"}],
    }
    try:
        repair = provider.run(
            system_prompt=system_prompt,
            input_payload=input_payload,
            schema=schema,
            schema_name=schema_name,
            corrective=corrective,
        )
    except OpenAICompatibleProviderError as error:
        return _fallback(
            deterministic_output,
            allow_fallback=allow_fallback,
            reason=f"provider_{error.category}",
            called_provider=True,
            capability_mode=capability_mode,
            repair_attempted=True,
            repair_succeeded=False,
            parse_status=parse_status,
            provider_response_id=provider_response_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
            validation_error_paths=_paths(last_issues),
        )

    provider_response_id = repair.provider_response_id or provider_response_id
    input_tokens += repair.input_tokens
    output_tokens += repair.output_tokens
    latency_ms += repair.latency_ms

    if repair.parsed is not None:
        repaired_issues = validate(repair.parsed)
        if not repaired_issues:
            return _valid_provider_outcome(
                repair.parsed,
                capability_mode,
                provider_response_id,
                input_tokens,
                output_tokens,
                latency_ms,
                canonicalized=False,
                repair_attempted=True,
                repair_succeeded=True,
            )
        canonical = _canonicalize(repair.parsed, validate)
        if canonical is not None:
            return _valid_provider_outcome(
                canonical,
                capability_mode,
                provider_response_id,
                input_tokens,
                output_tokens,
                latency_ms,
                canonicalized=True,
                repair_attempted=True,
                repair_succeeded=True,
            )
        last_issues = repaired_issues
        parse_status = "parsed"

    # ---- Deterministic fallback -----------------------------------------
    return _fallback(
        deterministic_output,
        allow_fallback=allow_fallback,
        reason="provider_output_contract_invalid",
        called_provider=True,
        capability_mode=capability_mode,
        repair_attempted=True,
        repair_succeeded=False,
        parse_status=parse_status,
        provider_response_id=provider_response_id,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        latency_ms=latency_ms,
        validation_error_paths=_paths(last_issues),
    )


def _valid_provider_outcome(
    output: dict[str, Any],
    capability_mode: str,
    provider_response_id: str | None,
    input_tokens: int,
    output_tokens: int,
    latency_ms: int,
    *,
    canonicalized: bool,
    repair_attempted: bool,
    repair_succeeded: bool,
) -> ContractExecutionOutcome:
    return ContractExecutionOutcome(
        output=output,
        status=str(output.get("status") or "ok"),
        called_provider=True,
        provider_output_valid=True,
        canonicalized=canonicalized,
        repair_attempted=repair_attempted,
        repair_succeeded=repair_succeeded,
        fallback_used=False,
        fallback_reason=None,
        capability_mode=capability_mode,
        provider_response_id=provider_response_id,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        latency_ms=latency_ms,
        parse_status="parsed",
        validation_error_paths=[],
    )


def _fallback(
    deterministic_output: Callable[[], dict[str, Any]],
    *,
    allow_fallback: bool,
    reason: str,
    called_provider: bool,
    capability_mode: str,
    repair_attempted: bool,
    repair_succeeded: bool,
    parse_status: str,
    provider_response_id: str | None,
    input_tokens: int,
    output_tokens: int,
    latency_ms: int,
    validation_error_paths: list[str],
) -> ContractExecutionOutcome:
    if not allow_fallback:
        raise NoDeterministicFallbackError(reason)
    output = deterministic_output()
    return ContractExecutionOutcome(
        output=output,
        status=str(output.get("status") or "ok"),
        called_provider=called_provider,
        provider_output_valid=False,
        canonicalized=False,
        repair_attempted=repair_attempted,
        repair_succeeded=repair_succeeded,
        fallback_used=True,
        fallback_reason=reason,
        capability_mode=capability_mode,
        provider_response_id=provider_response_id,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        latency_ms=latency_ms,
        parse_status=parse_status,
        validation_error_paths=validation_error_paths,
    )


def _canonicalize(output: Mapping[str, Any], validate: Validator) -> dict[str, Any] | None:
    """Return a lossless canonicalized copy if a documented status alias fixes it.

    Only the ``status`` field is normalized, and only when the rest of the output
    is already fully schema-valid (so no semantic content is manufactured).
    """

    status = output.get("status")
    if not isinstance(status, str):
        return None
    normalized = STATUS_ALIASES.get(status.strip().lower())
    if normalized is None or normalized == status:
        return None
    candidate = copy.deepcopy(dict(output))
    candidate["status"] = normalized
    if validate(candidate):
        return None
    return candidate


def _paths(issues: list[dict[str, str]]) -> list[str]:
    seen: list[str] = []
    for issue in issues:
        path = str(issue.get("path") or "(root)")
        if path not in seen:
            seen.append(path)
    return seen[:20]
