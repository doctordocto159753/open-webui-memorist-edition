from collections.abc import Sequence
from typing import Protocol

from pydantic import BaseModel, Field

from memcore.models import GateDecisionValue, TextUnitType

PIPELINE_VERSION = "memory-intelligence-v2"
PROMPT_BUNDLE_VERSION = "memorist-memory-worker-prompt-pack-v2"

JOB_TEXT_UNITIZATION = "text_unitization"
JOB_JAKOBSON_SENTENCE_ANALYSIS = "jakobson_sentence_analysis"
JOB_MEMORY_SIGNAL_ROUTING = "memory_signal_routing"
JOB_MEMORY_GATING = "memory_gating"
JOB_MEMORY_ANALYSIS = "memory_analysis"
JOB_CANDIDATE_EXTRACTION = "candidate_extraction"
JOB_HIGH_CONFIDENCE_EXTRACTION = "high_confidence_extraction"
JOB_MEMORY_CONSOLIDATION = "memory_consolidation"
JOB_GRAPH_PROJECTION = "graph_projection"


class UnitizedSpan(BaseModel):
    unit_index: int = Field(ge=0)
    unit_type: TextUnitType
    text: str = Field(min_length=1)
    start_char: int = Field(ge=0)
    end_char: int = Field(gt=0)
    language_code: str | None = None


class GateResult(BaseModel):
    decision: GateDecisionValue
    reason_codes: list[str]
    salience_score: float = Field(ge=0.0, le=1.0)
    persistence_score: float = Field(ge=0.0, le=1.0)
    actionability_score: float = Field(ge=0.0, le=1.0)
    sensitivity_score: float = Field(ge=0.0, le=1.0)
    novelty_score: float = Field(ge=0.0, le=1.0)
    requires_high_confidence_pass: bool = False


class CheapGateClassifier(Protocol):
    def classify(self, text: str, reason_codes: Sequence[str]) -> GateResult | None: ...
