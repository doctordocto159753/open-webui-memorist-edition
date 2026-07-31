from collections.abc import Generator
from dataclasses import replace
from typing import Any

import pytest

from memcore.config import get_settings
from memcore.memory_worker.execution import ContractExecutionOutcome


@pytest.fixture(autouse=True)
def clear_settings_cache(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> Generator[None, None, None]:
    if request.path.name not in {"test_config.py", "test_trusted_actor_authentication.py"}:
        monkeypatch.setenv("MEMORIST_ENV", "test")
        monkeypatch.setenv("MEMORIST_ALLOW_LEGACY_ACTOR_HEADERS_FOR_TESTS", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def wp02_downstream_semantic_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give pre-WP02 downstream tests an explicit semantic model fixture.

    Production deterministic fallback remains an abstention.  Tests whose
    subject is consolidation/retrieval/governance opt into this fixture so
    their seed messages still receive a valid model semantic response.  WP02
    contract, corpus, gate, parity and replay tests do not use this shortcut.
    """

    def execute_semantic_candidate_contract(
        *,
        profile: dict[str, Any],
        input_payload: Any,
        **_: Any,
    ) -> ContractExecutionOutcome:
        del profile
        payload = (
            input_payload.model_dump(mode="json")
            if hasattr(input_payload, "model_dump")
            else dict(input_payload)
        )
        raw = str(payload["current_raw_text"])
        sentences = payload["text_envelope"].get("sentences", [])
        units: list[dict[str, Any]] = []
        for index, sentence in enumerate(sentences):
            start = int(sentence["raw_start"])
            end = int(sentence["raw_end"])
            evidence = raw[start:end]
            if not evidence.strip():
                continue
            units.append(
                {
                    "id": f"downstream-semantic-{index}",
                    "raw_start": start,
                    "raw_end": end,
                    "evidence": evidence,
                    "proposition": evidence,
                    "unit_type": "statement",
                    "durability": "durable",
                    "polarity": "affirmed",
                    "epistemic_status": "asserted",
                }
            )
        output = {
            "schema_version": "1.0",
            "prompt_id": "memorist.semantic_candidate_analysis",
            "prompt_version": "1.1",
            "status": "ok" if units else "abstain",
            "warnings": [],
            "semantic_units": units,
            "references": [],
            "relations": [],
        }
        return ContractExecutionOutcome(
            output=output,
            status="succeeded" if units else "abstained",
            called_provider=True,
            provider_output_valid=True,
            canonicalized=False,
            repair_attempted=False,
            repair_succeeded=False,
            fallback_used=False,
            fallback_reason=None,
            capability_mode="test_semantic_model",
            provider_response_id="downstream-semantic-model",
            input_tokens=max(1, len(raw) // 4),
            output_tokens=len(units),
            latency_ms=0,
            parse_status="parsed",
            attempt_count=1,
            validation_error_paths=[],
        )

    monkeypatch.setattr(
        "memcore.memory_worker.semantic.orchestration.execute_semantic_candidate_contract",
        execute_semantic_candidate_contract,
    )

    from memcore.memory_worker.semantic.bounded_context import BoundedContextResolver

    original_resolve = BoundedContextResolver.resolve

    def resolve_with_test_actor(self: Any, *args: Any, **kwargs: Any) -> Any:
        result = original_resolve(self, *args, **kwargs)
        return result if result.authority_complete else replace(result, authority_complete=True)

    monkeypatch.setattr(BoundedContextResolver, "resolve", resolve_with_test_actor)
