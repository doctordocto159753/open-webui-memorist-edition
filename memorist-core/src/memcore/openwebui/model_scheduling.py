from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from memcore.model_control.resolution import RoleResolutionService
from memcore.model_control.storage import ModelControlStorage, model_control_repository
from memcore.models import ModelRole


@dataclass(frozen=True)
class ScheduledModelIdentity:
    model_role: str
    model_profile_uuid: str | None
    provider_type: str
    model_name: str
    workspace_uuid: str | None
    project_uuid: str | None

    def as_payload(self) -> dict[str, str | None]:
        return {
            "model_role": self.model_role,
            "model_profile_uuid": self.model_profile_uuid,
            "provider_type": self.provider_type,
            "model_name": self.model_name,
            "workspace_uuid": self.workspace_uuid,
            "project_uuid": self.project_uuid,
        }


def resolve_scoped_model_identity(
    connection: Any,
    session_uuid: str,
    role: ModelRole = ModelRole.MEMORY_EXTRACTION,
    *,
    repository: ModelControlStorage | None = None,
) -> ScheduledModelIdentity:
    session = connection.execute(
        "SELECT workspace_uuid, project_uuid FROM sessions WHERE session_uuid = ?",
        (session_uuid,),
    ).fetchone()
    if session is None:
        raise LookupError(f"session not found for model scheduling: {session_uuid}")
    workspace_uuid = _text(session["workspace_uuid"])
    project_uuid = _text(session["project_uuid"])
    selected_repository = repository or model_control_repository(connection)
    resolution = RoleResolutionService(selected_repository).resolve(
        role,
        workspace_uuid=workspace_uuid,
        project_uuid=project_uuid,
    )
    return ScheduledModelIdentity(
        model_role=role.value,
        model_profile_uuid=resolution.model_profile_uuid,
        provider_type=resolution.provider_type,
        model_name=resolution.model_name,
        workspace_uuid=workspace_uuid,
        project_uuid=project_uuid,
    )


def _text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
