from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from memcore.model_control.repository import built_in_default
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
) -> ScheduledModelIdentity:
    session = connection.execute(
        "SELECT workspace_uuid, project_uuid FROM sessions WHERE session_uuid = ?",
        (session_uuid,),
    ).fetchone()
    if session is None:
        raise LookupError(f"session not found for model scheduling: {session_uuid}")
    workspace_uuid = _text(session["workspace_uuid"])
    project_uuid = _text(session["project_uuid"])
    profile = connection.execute(
        """
        SELECT p.model_profile_uuid, p.provider_type, p.model_name
        FROM model_role_defaults AS d
        JOIN model_profiles AS p ON p.model_profile_uuid = d.model_profile_uuid
        WHERE d.role = ?
          AND p.role = d.role
          AND COALESCE(p.is_enabled, TRUE) = TRUE
          AND (
                (d.project_uuid IS NOT NULL AND d.project_uuid = ?)
             OR (
                d.project_uuid IS NULL
                AND d.workspace_uuid IS NOT NULL
                AND d.workspace_uuid = ?
             )
             OR (d.project_uuid IS NULL AND d.workspace_uuid IS NULL)
          )
          AND (
                COALESCE(p.requires_privacy_acknowledgement, FALSE) = FALSE
             OR p.privacy_acknowledged_at IS NOT NULL
          )
          AND (
                COALESCE(p.endpoint_is_local, TRUE) = TRUE
             OR p.privacy_acknowledged_at IS NOT NULL
          )
        ORDER BY CASE
            WHEN d.project_uuid IS NOT NULL AND d.project_uuid = ? THEN 0
            WHEN d.project_uuid IS NULL AND d.workspace_uuid = ? THEN 1
            ELSE 2
        END
        LIMIT 1
        """,
        (role.value, project_uuid, workspace_uuid, project_uuid, workspace_uuid),
    ).fetchone()
    if profile is None:
        fallback = built_in_default(role)
        return ScheduledModelIdentity(
            model_role=role.value,
            model_profile_uuid=None,
            provider_type=str(fallback["provider_type"]),
            model_name=str(fallback["model_name"]),
            workspace_uuid=workspace_uuid,
            project_uuid=project_uuid,
        )
    return ScheduledModelIdentity(
        model_role=role.value,
        model_profile_uuid=str(profile["model_profile_uuid"]),
        provider_type=str(profile["provider_type"]),
        model_name=str(profile["model_name"]),
        workspace_uuid=workspace_uuid,
        project_uuid=project_uuid,
    )


def _text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
