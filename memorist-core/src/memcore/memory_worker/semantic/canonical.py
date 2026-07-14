from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from memcore.models import (
    GateDecisionValue,
    JakobsonConfidence,
    JakobsonFunction,
    MemorySignalRouteStatus,
    MemorySignalRouteType,
)


class CanonicalSemanticRuntime(StrEnum):
    LITE = "lite"
    FULL = "full"


class CanonicalSemanticAuthority(StrEnum):
    JAKOBSON = "jakobson_canonical"


class CanonicalSemanticModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CanonicalSemanticFactor(CanonicalSemanticModel):
    """One Jakobson communication factor as validated semantic evidence."""

    value: str | None = None
    evidence: str | None = None
    confidence: JakobsonConfidence = JakobsonConfidence.MEDIUM


class CanonicalGateDecision(CanonicalSemanticModel):
    """Gate output normalized for semantic parity checks."""

    decision: GateDecisionValue
    reason_codes: tuple[str, ...] = Field(default_factory=tuple)
    salience_score: float = Field(ge=0.0, le=1.0)
    persistence_score: float = Field(ge=0.0, le=1.0)
    actionability_score: float = Field(ge=0.0, le=1.0)
    sensitivity_score: float = Field(ge=0.0, le=1.0)
    novelty_score: float = Field(ge=0.0, le=1.0)
    requires_high_confidence_pass: bool = False

    @property
    def allows_analysis(self) -> bool:
        return self.decision in {
            GateDecisionValue.ANALYZE,
            GateDecisionValue.ANALYZE_HIGH_CONFIDENCE,
            GateDecisionValue.MANUAL_REVIEW,
        }


class CanonicalRouteDecision(CanonicalSemanticModel):
    """Route output normalized independently of SQLite/PostgreSQL persistence."""

    route_type: MemorySignalRouteType
    status: MemorySignalRouteStatus
    extractor_id: str
    priority: int = Field(ge=0, le=100)
    confidence: JakobsonConfidence
    reason: str

    @model_validator(mode="after")
    def validate_ignore_route_status(self) -> Self:
        route_is_ignore = self.route_type is MemorySignalRouteType.IGNORE
        status_is_ignored = self.status is MemorySignalRouteStatus.IGNORED
        if route_is_ignore and not status_is_ignored:
            raise ValueError("ignore routes must be recorded with ignored status")
        return self

    @property
    def is_ready_memory_route(self) -> bool:
        return (
            self.route_type is not MemorySignalRouteType.IGNORE
            and self.status is MemorySignalRouteStatus.READY
        )


class CanonicalLinguisticComplements(CanonicalSemanticModel):
    """StructuredAnalyzer data that supplements, but does not own, routing authority."""

    speech_acts: tuple[str, ...] = Field(default_factory=tuple)
    conceptual_nuclei: tuple[str, ...] = Field(default_factory=tuple)
    entity_mentions: tuple[str, ...] = Field(default_factory=tuple)
    temporal_expressions: tuple[str, ...] = Field(default_factory=tuple)
    modality_markers: tuple[str, ...] = Field(default_factory=tuple)
    negated: bool | None = None
    abstained: bool = False
    raw_schema_version: str | None = None

    @property
    def owns_semantic_authority(self) -> bool:
        return False


class CanonicalSemanticProvenance(CanonicalSemanticModel):
    runtime: CanonicalSemanticRuntime
    source_pipeline: str
    analysis_run_uuid: str | None = None
    processing_run_uuid: str | None = None
    prompt_execution_uuid: str | None = None
    prompt_id: str | None = None
    prompt_version: str | None = None
    provider_type: str | None = None
    model_name: str | None = None
    model_profile_uuid: str | None = None


class CanonicalSemanticDecision(CanonicalSemanticModel):
    """Canonical per-text-unit semantic authority contract for PR4-D.

    The contract is pure and runtime-neutral. Task 02 deliberately does not wire it into
    Lite or Full execution yet; later tasks will use it as the handoff from Jakobson,
    routing, gate, and complementary linguistic analysis into candidate creation.
    """

    schema_version: str = "pr4d.semantic_decision.v1"
    semantic_authority: CanonicalSemanticAuthority = CanonicalSemanticAuthority.JAKOBSON
    message_uuid: str
    text_unit_uuid: str
    unit_index: int = Field(ge=0)
    speaker_role: str
    text: str = Field(min_length=1)
    dominant_function: JakobsonFunction
    secondary_functions: tuple[JakobsonFunction, ...] = Field(default_factory=tuple)
    sender: CanonicalSemanticFactor
    receiver: CanonicalSemanticFactor
    message: CanonicalSemanticFactor
    context: CanonicalSemanticFactor
    code: CanonicalSemanticFactor
    contact_channel: CanonicalSemanticFactor
    gate: CanonicalGateDecision
    routes: tuple[CanonicalRouteDecision, ...] = Field(min_length=1)
    linguistic_complements: CanonicalLinguisticComplements = Field(
        default_factory=CanonicalLinguisticComplements
    )
    provenance: CanonicalSemanticProvenance
    warnings: tuple[str, ...] = Field(default_factory=tuple)

    @field_validator("secondary_functions")
    @classmethod
    def reject_duplicate_secondary_functions(
        cls, value: tuple[JakobsonFunction, ...]
    ) -> tuple[JakobsonFunction, ...]:
        if len(set(value)) != len(value):
            raise ValueError("secondary_functions must not contain duplicates")
        return value

    @model_validator(mode="after")
    def validate_secondary_functions_do_not_repeat_dominant(self) -> Self:
        if self.dominant_function in self.secondary_functions:
            raise ValueError("secondary_functions must not repeat dominant_function")
        return self

    @property
    def ready_route_types(self) -> tuple[MemorySignalRouteType, ...]:
        return tuple(route.route_type for route in self.routes if route.is_ready_memory_route)

    @property
    def has_ready_memory_route(self) -> bool:
        return bool(self.ready_route_types)

    @property
    def candidate_creation_blockers(self) -> tuple[str, ...]:
        blockers: list[str] = []
        blocked_gate_decisions = {GateDecisionValue.DISCARD, GateDecisionValue.RETAIN_RAW_ONLY}
        if self.gate.decision in blocked_gate_decisions:
            blockers.append(f"gate:{self.gate.decision.value}")
        if not self.has_ready_memory_route:
            blockers.append("route:none_ready")
        return tuple(blockers)

    @property
    def allows_candidate_creation(self) -> bool:
        return not self.candidate_creation_blockers
