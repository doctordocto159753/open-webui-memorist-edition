from typing import Any

from pydantic import BaseModel, Field, model_validator


class ContextUnit(BaseModel):
    text_unit_uuid: str
    text: str
    unit_index: int
    speaker_role: str


class AnalysisRequest(BaseModel):
    text_unit_uuid: str
    text: str
    speaker_role: str
    source_timestamp: str | None = None
    session_uuid: str | None = None
    project_uuid: str | None = None
    previous_units: list[ContextUnit] = Field(default_factory=list)
    next_units: list[ContextUnit] = Field(default_factory=list)


class AnalysisResponse(BaseModel):
    speech_acts: list[str]
    jakobson_functions: dict[str, float]
    conceptual_nuclei: list[dict[str, Any]]
    entity_mentions: list[dict[str, Any]]
    temporal_expressions: list[dict[str, Any]]
    modality: dict[str, Any]
    memory_signals: list[str]
    abstention: dict[str, Any]

    @model_validator(mode="after")
    def reject_hidden_reasoning(self) -> "AnalysisResponse":
        forbidden = {"chain_of_thought", "reasoning", "hidden_reasoning", "scratchpad"}
        payload = self.model_dump()
        found = forbidden.intersection(payload)
        if found:
            raise ValueError(f"analysis output contains forbidden reasoning field: {sorted(found)}")
        return self


RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": [
        "speech_acts",
        "jakobson_functions",
        "conceptual_nuclei",
        "entity_mentions",
        "temporal_expressions",
        "modality",
        "memory_signals",
        "abstention",
    ],
}
