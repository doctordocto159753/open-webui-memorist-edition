from __future__ import annotations

import pytest
from pydantic import ValidationError

from memcore.memory_worker.semantic import (
    CanonicalGateDecision,
    CanonicalLinguisticComplements,
    CanonicalRouteDecision,
    CanonicalSemanticDecision,
    CanonicalSemanticFactor,
    CanonicalSemanticProvenance,
)
from memcore.memory_worker.semantic.canonical import CanonicalSemanticRuntime
from memcore.models import (
    GateDecisionValue,
    JakobsonConfidence,
    JakobsonFunction,
    MemorySignalRouteStatus,
    MemorySignalRouteType,
)


def _factor(value: str | None = "value") -> CanonicalSemanticFactor:
    return CanonicalSemanticFactor(value=value, evidence="evidence", confidence=JakobsonConfidence.HIGH)


def _gate(decision: GateDecisionValue) -> CanonicalGateDecision:
    return CanonicalGateDecision(
        decision=decision,
        reason_codes=(f"reason:{decision.value}",),
        salience_score=0.8,
        persistence_score=0.7,
        actionability_score=0.6,
        sensitivity_score=0.1,
        novelty_score=0.5,
        requires_high_confidence_pass=decision is GateDecisionValue.ANALYZE_HIGH_CONFIDENCE,
    )


def _route(
    route_type: MemorySignalRouteType,
    status: MemorySignalRouteStatus = MemorySignalRouteStatus.READY,
) -> CanonicalRouteDecision:
    return CanonicalRouteDecision(
        route_type=route_type,
        status=status,
        extractor_id=f"{route_type.value}_extractor.v1",
        priority=75,
        confidence=JakobsonConfidence.HIGH,
        reason="test route",
    )


def _provenance(runtime: CanonicalSemanticRuntime = CanonicalSemanticRuntime.LITE) -> CanonicalSemanticProvenance:
    return CanonicalSemanticProvenance(
        runtime=runtime,
        source_pipeline="pr4d.task02.test",
        analysis_run_uuid="analysis-run",
        processing_run_uuid="processing-run",
        prompt_execution_uuid="prompt-execution",
        prompt_id="jakobson_sentence_analysis",
        prompt_version="v1",
        provider_type="deterministic",
        model_name="deterministic_extraction",
    )


def _decision(
    *,
    dominant: JakobsonFunction = JakobsonFunction.CONATIVE,
    gate: GateDecisionValue = GateDecisionValue.ANALYZE_HIGH_CONFIDENCE,
    routes: tuple[CanonicalRouteDecision, ...] | None = None,
) -> CanonicalSemanticDecision:
    return CanonicalSemanticDecision(
        message_uuid="message-uuid",
        text_unit_uuid="unit-uuid",
        unit_index=0,
        speaker_role="user",
        text="Always use the project glossary.",
        dominant_function=dominant,
        secondary_functions=(JakobsonFunction.METALINGUAL,),
        sender=_factor("user"),
        receiver=_factor("AI"),
        message=_factor("Always use the project glossary."),
        context=_factor("project workflow"),
        code=_factor("English; project terminology"),
        contact_channel=_factor("chat"),
        gate=_gate(gate),
        routes=routes or (_route(MemorySignalRouteType.TASK_CONSTRAINT),),
        linguistic_complements=CanonicalLinguisticComplements(
            speech_acts=("directive",),
            conceptual_nuclei=("project glossary",),
            entity_mentions=("project glossary",),
            modality_markers=("always",),
            negated=False,
            raw_schema_version="structured-v1",
        ),
        provenance=_provenance(),
        warnings=("baseline-only",),
    )


def test_canonical_decision_allows_candidate_creation_for_analyzed_ready_route() -> None:
    decision = _decision()

    assert decision.gate.allows_analysis is True
    assert decision.ready_route_types == (MemorySignalRouteType.TASK_CONSTRAINT,)
    assert decision.has_ready_memory_route is True
    assert decision.candidate_creation_blockers == ()
    assert decision.allows_candidate_creation is True
    assert decision.linguistic_complements.owns_semantic_authority is False


def test_phatic_ignore_decision_blocks_candidate_creation_without_runtime_side_effects() -> None:
    decision = _decision(
        dominant=JakobsonFunction.PHATIC,
        gate=GateDecisionValue.DISCARD,
        routes=(
            _route(
                MemorySignalRouteType.IGNORE,
                status=MemorySignalRouteStatus.IGNORED,
            ),
        ),
    )

    assert decision.gate.allows_analysis is False
    assert decision.ready_route_types == ()
    assert decision.candidate_creation_blockers == ("gate:discard", "route:none_ready")
    assert decision.allows_candidate_creation is False


def test_raw_only_referential_decision_preserves_route_but_blocks_candidate_creation() -> None:
    decision = _decision(
        dominant=JakobsonFunction.REFERENTIAL,
        gate=GateDecisionValue.RETAIN_RAW_ONLY,
        routes=(_route(MemorySignalRouteType.PROCESS_FACT),),
    )

    assert decision.has_ready_memory_route is True
    assert decision.candidate_creation_blockers == ("gate:retain_raw_only",)
    assert decision.allows_candidate_creation is False


def test_contract_is_frozen_extra_forbidden_and_json_round_trippable() -> None:
    decision = _decision()
    round_tripped = CanonicalSemanticDecision.model_validate_json(decision.model_dump_json())

    assert round_tripped == decision
    with pytest.raises(ValidationError):
        decision.text = "mutated"
    with pytest.raises(ValidationError):
        CanonicalSemanticDecision.model_validate({**decision.model_dump(mode="json"), "extra": True})


def test_invalid_route_and_secondary_function_shapes_are_rejected() -> None:
    with pytest.raises(ValidationError):
        _route(MemorySignalRouteType.IGNORE, status=MemorySignalRouteStatus.READY)

    with pytest.raises(ValidationError):
        CanonicalSemanticDecision(
            **{
                **_decision().model_dump(mode="python"),
                "secondary_functions": (JakobsonFunction.CONATIVE,),
            }
        )
