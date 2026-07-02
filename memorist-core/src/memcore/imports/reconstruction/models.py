from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from memcore.imports.models import ImportIssue


class ImportedRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"
    DEVELOPER = "developer"
    UNKNOWN = "unknown"


class ImportedContentPart(BaseModel):
    part_type: str
    text: str | None = None
    raw: Any = None
    quarantined: bool = False


class ImportedMessageNode(BaseModel):
    source_message_id: str | None
    parent_ids: list[str] = Field(default_factory=list)
    child_ids: list[str] = Field(default_factory=list)
    role: ImportedRole
    creator_label: str | None = None
    content_parts: list[ImportedContentPart] = Field(default_factory=list)
    created_at: datetime | str | None = None
    timestamp_confidence: str = "missing"
    model_name: str | None = None
    branch_status: str = "active"
    metadata: dict[str, Any] = Field(default_factory=dict)


class ImportedConversation(BaseModel):
    source_platform: str
    source_conversation_id: str | None = None
    title: str | None = None
    created_at: datetime | str | None = None
    updated_at: datetime | str | None = None
    roots: list[str] = Field(default_factory=list)
    active_leaf_id: str | None = None
    messages: dict[str, ImportedMessageNode] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    reconstruction_issues: list[ImportIssue] = Field(default_factory=list)
