from collections.abc import Iterator
from typing import Any, Protocol

from pydantic import BaseModel, Field


class StagedArtifact(BaseModel):
    relative_path: str
    object_store_path: str
    artifact_role: str
    detected_media_type: str | None
    size_bytes: int = Field(ge=0)
    sha256: str


class FormatProbe(BaseModel):
    adapter_id: str
    adapter_version: str
    confidence: float = Field(ge=0.0, le=1.0)
    detected_format: str
    detected_format_version: str | None = None
    evidence: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ImportRecord(BaseModel):
    source_record_type: str
    source_record_id: str | None = None
    source_parent_id: str | None = None
    raw_payload: dict[str, Any]
    normalized_payload: dict[str, Any]
    reconstruction_confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class ImportIssue(BaseModel):
    severity: str
    issue_code: str
    message: str
    artifact_path: str | None = None
    source_record_id: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class ImportValidationReport(BaseModel):
    issues: list[ImportIssue] = Field(default_factory=list)
    warning_count: int = 0
    error_count: int = 0


class ImportAdapter(Protocol):
    adapter_id: str
    adapter_version: str

    def probe(self, artifacts: list[StagedArtifact]) -> FormatProbe: ...

    def parse(self, artifacts: list[StagedArtifact]) -> Iterator[ImportRecord]: ...

    def validate(self, records: list[ImportRecord]) -> ImportValidationReport: ...
