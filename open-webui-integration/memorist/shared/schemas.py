from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ResolvedSession:
    session_uuid: str
    workspace_uuid: str | None = None
    project_uuid: str | None = None


@dataclass(frozen=True)
class CapturedMessage:
    session_uuid: str
    message_uuid: str
    duplicate: bool = False


@dataclass(frozen=True)
class PreflightResult:
    status: str
    attachment_uuid: str | None = None
    retrieval_run_uuid: str | None = None
    rendered_attachment: str | None = None
    token_count: int = 0
    effective_token_budget: int = 0
    budget_reason: str | None = None
    token_estimation_method: str | None = None
    attachment_mode: str | None = None
    attachment_review: bool = False


@dataclass(frozen=True)
class ResolvedTurnPolicy:
    mode: str
    capture_enabled: bool
    recall_enabled: bool
    attachment_enabled: bool
    private: bool
    source: str
    attachment_review: bool
    runtime_profile: str


def as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}
