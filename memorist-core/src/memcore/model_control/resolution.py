from __future__ import annotations

import os
from typing import Any, Protocol

from pydantic import BaseModel

from memcore.model_control.repository import (
    built_in_default,
    public_profile,
    requires_privacy_acknowledgement,
)
from memcore.model_control.roles import MODEL_ROLE_SPECS
from memcore.models import ModelProfile, ModelRole, utc_now

ROLE_RESOLUTION_VERSION = "processing-role-resolution-v1"

ROLE_INHERITANCE: dict[ModelRole, ModelRole] = {
    ModelRole.HIGH_CONFIDENCE_EXTRACTION: ModelRole.MEMORY_EXTRACTION,
    ModelRole.IMPORT_RECONSTRUCTION: ModelRole.MEMORY_EXTRACTION,
    ModelRole.BLOCK_COMPACTION: ModelRole.MEMORY_EXTRACTION,
    ModelRole.PRIVACY_SENSITIVITY: ModelRole.MEMORY_EXTRACTION,
}


class RoleResolutionRepository(Protocol):
    def resolve_default(
        self,
        role: ModelRole | str,
        workspace_uuid: str | None = None,
        project_uuid: str | None = None,
    ) -> dict[str, Any] | None: ...

    def get_profile(self, model_profile_uuid: str) -> ModelProfile | None: ...

    def profile_certification(self, profile: ModelProfile | str) -> dict[str, Any]: ...


class EffectiveRoleResolution(BaseModel):
    requested_role: ModelRole
    effective_role: ModelRole
    model_profile_uuid: str | None
    provider_type: str
    model_name: str
    endpoint_url: str | None = None
    endpoint_is_local: bool
    scope_source: str
    inheritance_source: str | None = None
    secret_reference_configured: bool
    secret_reference_available: bool
    authentication_status: str
    certification_status: str
    certification_current: bool
    privacy_acknowledgement_required: bool
    privacy_acknowledged: bool
    capability_compatible: bool
    capability_reasons: list[str]
    fallback_reason: str | None = None
    built_in: bool = False
    fail_open: bool
    resolution_timestamp: str
    resolution_version: str = ROLE_RESOLUTION_VERSION

    def runtime_profile(self) -> dict[str, Any]:
        """Return the secret-reference-bearing profile used only in Core."""

        return {
            **self.model_dump(mode="json"),
            "role": self.requested_role.value,
            "model_role": self.requested_role.value,
            "effective_role": self.effective_role.value,
            "is_enabled": self.provider_type != "disabled",
        }


class RoleResolutionService:
    def __init__(self, repository: RoleResolutionRepository) -> None:
        self.repository = repository

    def resolve(
        self,
        role: ModelRole | str,
        *,
        workspace_uuid: str | None = None,
        project_uuid: str | None = None,
    ) -> EffectiveRoleResolution:
        requested = role if isinstance(role, ModelRole) else ModelRole(role)
        if requested is ModelRole.MAIN_CHAT_OBSERVED:
            return self._built_in(requested, fallback_reason="controlled_by_openwebui")

        configured = self._configured(requested, workspace_uuid, project_uuid)
        if configured is not None:
            profile, scope_source, certification = configured
            unusable_reason = _profile_unusable_reason(
                profile,
                requested,
                expected_profile_role=requested,
                certification_status=str(certification["certification_status"]),
            )
            if unusable_reason is None:
                return self._from_profile(
                    requested,
                    requested,
                    profile,
                    scope_source=scope_source,
                    certification=certification,
                )

        inherited_role = ROLE_INHERITANCE.get(requested)
        inherited_unusable_reason: str | None = None
        if inherited_role is not None:
            inherited = self._configured(inherited_role, workspace_uuid, project_uuid)
            if inherited is not None:
                profile, scope_source, certification = inherited
                unusable_reason = _profile_unusable_reason(
                    profile,
                    requested,
                    expected_profile_role=inherited_role,
                    certification_status=str(certification["certification_status"]),
                )
                inherited_unusable_reason = unusable_reason
                if unusable_reason is None:
                    return self._from_profile(
                        requested,
                        inherited_role,
                        profile,
                        scope_source=scope_source,
                        inheritance_source=inherited_role.value,
                        certification=certification,
                    )

        fallback_reason = "no_configured_default"
        if configured is not None:
            fallback_reason = (
                _profile_unusable_reason(
                    configured[0],
                    requested,
                    expected_profile_role=requested,
                    certification_status=str(configured[2]["certification_status"]),
                )
                or fallback_reason
            )
        elif inherited_unusable_reason is not None:
            fallback_reason = inherited_unusable_reason
        return self._built_in(requested, fallback_reason=fallback_reason)

    def _configured(
        self,
        role: ModelRole,
        workspace_uuid: str | None,
        project_uuid: str | None,
    ) -> tuple[ModelProfile, str, dict[str, Any]] | None:
        resolved = self.repository.resolve_default(role, workspace_uuid, project_uuid)
        if resolved is None or resolved.get("model_profile_uuid") is None:
            return None
        profile = self.repository.get_profile(str(resolved["model_profile_uuid"]))
        if profile is None:
            return None
        return (
            profile,
            _scope_source(resolved, workspace_uuid, project_uuid),
            self.repository.profile_certification(profile),
        )

    def _from_profile(
        self,
        requested: ModelRole,
        effective: ModelRole,
        profile: ModelProfile,
        *,
        scope_source: str,
        certification: dict[str, Any],
        inheritance_source: str | None = None,
    ) -> EffectiveRoleResolution:
        compatible, reasons = _capability_compatibility(requested, profile)
        ack_required = requires_privacy_acknowledgement(profile)
        secret_configured = bool(profile.secret_env_var_name)
        secret_available = not secret_configured or bool(
            os.environ.get(str(profile.secret_env_var_name))
        )
        return EffectiveRoleResolution(
            requested_role=requested,
            effective_role=effective,
            model_profile_uuid=profile.model_profile_uuid,
            provider_type=profile.provider_type,
            model_name=profile.model_name,
            endpoint_url=profile.endpoint_url,
            endpoint_is_local=profile.endpoint_is_local,
            scope_source=scope_source,
            inheritance_source=inheritance_source,
            secret_reference_configured=secret_configured,
            secret_reference_available=secret_available,
            authentication_status=str(certification["authentication_status"]),
            certification_status=str(certification["certification_status"]),
            certification_current=bool(certification["certification_current"]),
            privacy_acknowledgement_required=ack_required,
            privacy_acknowledged=not ack_required or profile.privacy_acknowledged_at is not None,
            capability_compatible=compatible,
            capability_reasons=reasons,
            fail_open=MODEL_ROLE_SPECS[requested].fail_open,
            resolution_timestamp=utc_now(),
        )

    def _built_in(
        self,
        requested: ModelRole,
        *,
        fallback_reason: str,
    ) -> EffectiveRoleResolution:
        fallback = built_in_default(requested)
        compatible = fallback["provider_type"] != "disabled" or requested is ModelRole.EMBEDDING
        return EffectiveRoleResolution(
            requested_role=requested,
            effective_role=requested,
            model_profile_uuid=None,
            provider_type=str(fallback["provider_type"]),
            model_name=str(fallback["model_name"]),
            endpoint_is_local=True,
            scope_source="built_in_fallback",
            secret_reference_configured=False,
            secret_reference_available=True,
            authentication_status="not_required",
            certification_status="not_required",
            certification_current=True,
            privacy_acknowledgement_required=False,
            privacy_acknowledged=True,
            capability_compatible=compatible,
            capability_reasons=[] if compatible else ["built_in_role_disabled"],
            fallback_reason=fallback_reason,
            built_in=True,
            fail_open=MODEL_ROLE_SPECS[requested].fail_open,
            resolution_timestamp=utc_now(),
        )


def public_resolution(
    resolution: EffectiveRoleResolution,
    repository: RoleResolutionRepository,
) -> dict[str, Any]:
    payload = resolution.model_dump(mode="json")
    payload["role"] = resolution.requested_role.value
    profile = (
        repository.get_profile(resolution.model_profile_uuid)
        if resolution.model_profile_uuid
        else None
    )
    payload["effective_profile"] = (
        public_profile(profile, repository.profile_certification(profile))
        if profile is not None
        else None
    )
    # Endpoint URLs may be shown in their redacted public form via effective_profile.
    payload.pop("endpoint_url", None)
    return payload


def _scope_source(
    resolved: dict[str, Any],
    workspace_uuid: str | None,
    project_uuid: str | None,
) -> str:
    # resolve_default returns the profile rather than the default row.  The
    # requested coordinates still identify which precedence arm could win.
    if project_uuid:
        project = resolved.get("project_uuid")
        if project == project_uuid:
            return "project"
    if workspace_uuid:
        workspace = resolved.get("workspace_uuid")
        if workspace == workspace_uuid:
            return "workspace"
    return "global"


def _profile_unusable_reason(
    profile: ModelProfile,
    requested: ModelRole,
    *,
    expected_profile_role: ModelRole,
    certification_status: str,
) -> str | None:
    if profile.role is not expected_profile_role:
        return "profile_role_mismatch"
    if not profile.is_enabled:
        return "configured_profile_disabled"
    if requires_privacy_acknowledgement(profile) and profile.privacy_acknowledged_at is None:
        return "privacy_acknowledgement_missing"
    if profile.secret_env_var_name and not os.environ.get(profile.secret_env_var_name):
        return "secret_reference_unavailable"
    if certification_status != "current":
        return f"provider_certification_{certification_status}"
    compatible, reasons = _capability_compatibility(requested, profile)
    if not compatible:
        return reasons[0] if reasons else "role_capability_incompatible"
    return None


def _capability_compatibility(
    requested: ModelRole,
    profile: ModelProfile,
) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if requested is ModelRole.EMBEDDING:
        compatible = profile.provider_type in {
            "deterministic",
            "local_embedding",
            "openai_compatible",
            "openai_compatible_embedding",
            "ollama_embedding",
        } and (
            profile.supports_embeddings
            or profile.provider_type in {"deterministic", "local_embedding", "ollama_embedding"}
        )
        if not compatible:
            reasons.append("embedding_capability_not_declared")
        return compatible, reasons
    if profile.provider_type in {"openai_compatible_embedding", "ollama_embedding"}:
        reasons.append("embedding_only_profile_for_llm_role")
        return False, reasons
    if profile.provider_type in {"openai_compatible", "openai_compatible_llm"} and not (
        profile.supports_json_mode or profile.supports_structured_output
    ):
        reasons.append("structured_output_capability_not_declared")
        return False, reasons
    return profile.provider_type != "unknown", reasons
