from __future__ import annotations

from typing import Any, Protocol

from memcore.model_control.resolution import RoleResolutionService
from memcore.model_control.roles import MODEL_ROLE_SPECS
from memcore.models import ModelRole


class SetupRepository(Protocol):
    def resolve_default(
        self,
        role: ModelRole | str,
        workspace_uuid: str | None = None,
        project_uuid: str | None = None,
    ) -> dict[str, Any] | None: ...

    def get_profile(self, model_profile_uuid: str) -> Any: ...

    def health(self) -> dict[str, Any]: ...

    def usage_summary(self) -> dict[str, Any]: ...

    def profile_certification(self, profile: Any) -> dict[str, Any]: ...


SETUP_ROLES = (
    ModelRole.PREFLIGHT,
    ModelRole.MEMORY_EXTRACTION,
    ModelRole.HIGH_CONFIDENCE_EXTRACTION,
    ModelRole.PRIVACY_SENSITIVITY,
    ModelRole.EMBEDDING,
    ModelRole.BLOCK_COMPACTION,
    ModelRole.IMPORT_RECONSTRUCTION,
)
REQUIRED_PROCESSING_ROLES = (ModelRole.MEMORY_EXTRACTION,)
RECOMMENDED_FIRST_RUN_ROLES = (
    ModelRole.MEMORY_EXTRACTION,
    ModelRole.HIGH_CONFIDENCE_EXTRACTION,
)


def build_setup_status(
    repository: SetupRepository,
    *,
    runtime_profile: str,
    workspace_uuid: str | None,
) -> dict[str, Any]:
    role_items: list[dict[str, Any]] = []
    configured_roles: list[str] = []
    fallback_roles: list[str] = []
    missing_roles: list[str] = []

    resolver = RoleResolutionService(repository)
    health_items = repository.health().get("latest_health_events", [])
    health_by_profile = {
        str(item["model_profile_uuid"]): item
        for item in health_items
        if item.get("model_profile_uuid")
    }
    usage_items = repository.usage_summary().get("by_role", [])
    usage_by_role = {str(item["role"]): item for item in usage_items}
    wired_roles = {role.value for role in SETUP_ROLES}

    for role in SETUP_ROLES:
        configured = repository.resolve_default(role, workspace_uuid, None)
        resolution = resolver.resolve(role, workspace_uuid=workspace_uuid)
        effective = resolution.runtime_profile()
        effective_profile = (
            repository.get_profile(resolution.model_profile_uuid)
            if resolution.model_profile_uuid
            else None
        )
        configured_profile = (
            repository.get_profile(str(configured["model_profile_uuid"]))
            if configured is not None and configured.get("model_profile_uuid")
            else None
        )
        status_profile = configured_profile or effective_profile
        certification = (
            repository.profile_certification(status_profile)
            if status_profile is not None
            else {
                "secret_reference_configured": False,
                "secret_available_in_core": True,
                "authentication_status": "not_required",
                "certification_status": "not_required",
                "certification_current": True,
            }
        )
        available = bool(
            effective.get("is_enabled", True)
            and effective.get("provider_type") not in {None, "disabled", "unknown"}
        )
        if configured is not None:
            configured_roles.append(role.value)
        elif available:
            fallback_roles.append(role.value)
        if role in REQUIRED_PROCESSING_ROLES and not available:
            missing_roles.append(role.value)

        spec = MODEL_ROLE_SPECS[role]
        role_items.append(
            {
                "role": role.value,
                "title": spec.title,
                "required": role in REQUIRED_PROCESSING_ROLES,
                "recommended": role in RECOMMENDED_FIRST_RUN_ROLES,
                "configured": configured is not None,
                "available": available,
                "source": (
                    f"inherited_from_{resolution.inheritance_source}"
                    if resolution.inheritance_source
                    else (
                        "configured_default"
                        if configured is not None
                        and resolution.model_profile_uuid == configured.get("model_profile_uuid")
                        else "configured_unusable"
                    )
                    if configured is not None
                    else "built_in_fallback"
                ),
                "scope_source": resolution.scope_source,
                "inheritance_source": resolution.inheritance_source,
                "fallback_reason": resolution.fallback_reason,
                "effective_role": resolution.effective_role.value,
                "provider_type": effective.get("provider_type"),
                "provider_name": (
                    effective_profile.provider_name
                    if effective_profile is not None
                    else effective.get("provider_type")
                ),
                "model_name": effective.get("model_name"),
                "model_profile_uuid": effective.get("model_profile_uuid"),
                "endpoint_is_local": resolution.endpoint_is_local,
                "secret_configured": certification["secret_reference_configured"],
                "secret_available": certification["secret_available_in_core"],
                "secret_reference_configured": certification["secret_reference_configured"],
                "secret_available_in_core": certification["secret_available_in_core"],
                "authentication_status": certification["authentication_status"],
                "certification_status": certification["certification_status"],
                "certification_current": certification["certification_current"],
                "privacy_acknowledged": resolution.privacy_acknowledged,
                "capability_compatible": resolution.capability_compatible,
                "capability_reasons": resolution.capability_reasons,
                "supports_structured_output": bool(
                    effective_profile is not None and effective_profile.supports_structured_output
                ),
                "supports_embeddings": bool(
                    effective_profile is not None and effective_profile.supports_embeddings
                ),
                "description": spec.description,
                "safe_fallback": spec.safe_beta_default,
                "runtime_wired": role.value in wired_roles,
                "last_health": (
                    health_by_profile.get(str(resolution.model_profile_uuid))
                    if resolution.model_profile_uuid
                    else None
                ),
                "last_runtime_use": usage_by_role.get(role.value),
            }
        )

    recommended_missing = [
        role.value for role in RECOMMENDED_FIRST_RUN_ROLES if role.value not in configured_roles
    ]
    return {
        "memory_setup_required": bool(missing_roles),
        "ready_for_memory_processing": not missing_roles,
        "recommended_setup": bool(recommended_missing),
        "configured_roles": configured_roles,
        "fallback_roles": fallback_roles,
        "missing_roles": missing_roles,
        "recommended_missing_roles": recommended_missing,
        "local_fallback_available": all(
            item["available"]
            for item in role_items
            if item["role"] in {role.value for role in REQUIRED_PROCESSING_ROLES}
        ),
        "runtime_profile": runtime_profile,
        "scope": "workspace" if workspace_uuid else "global",
        "roles": role_items,
        "secret_strategy": "env_var_reference",
        "secret_values_returned": False,
        "full_mode_note": (
            "Full mode keeps deterministic extraction fallback; configure an embedding "
            "profile only when semantic vector retrieval is enabled."
            if runtime_profile == "full"
            else None
        ),
    }
