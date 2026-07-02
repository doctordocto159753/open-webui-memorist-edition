from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

SegmentationConfidence = Literal["high", "medium", "low"]


class SentenceUnit(BaseModel):
    unit_uuid: str
    unit_type: Literal["sentence"] = "sentence"
    sentence_index: int = Field(ge=1)
    text: str = Field(min_length=1)
    char_start: int = Field(ge=0)
    char_end: int = Field(gt=0)
    content_hash: str
    language_hint: str | None = None
    segmentation_confidence: SegmentationConfidence = "high"
    segmentation_notes: str | None = None

    @model_validator(mode="after")
    def validate_offsets(self) -> SentenceUnit:
        if self.char_end <= self.char_start:
            raise ValueError("char_end must be greater than char_start")
        if self.char_end - self.char_start != len(self.text):
            raise ValueError("sentence text length must match char_start/char_end")
        return self


class SentenceSegmentation(BaseModel):
    message_uuid: str
    units: list[SentenceUnit]
