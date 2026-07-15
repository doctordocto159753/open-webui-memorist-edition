from __future__ import annotations

from typing import Any, Protocol

from memcore.model_control.repository import built_in_default
from memcore.model_control.roles import MODEL_ROLE_SPECS
from memcore.models import ModelRole


class SetupRepository(Protocol):
    def resolve_default(
        self,
        role: ModelRole | str,
        workspace_uuid: str | None = None,
        project_uuid: str | None = None,
    ) -> dict[str, Any] | None:
        ...


SETUP_ROLES = (
    ModelRole.MEMORY_EXTRACTION,
    ModelRole.HIGH_CONFIDENCE_EXTRACTION,
    ModelRole.EMBEDDING,
    ModelRole.PRIVACY_SENSITIVITY,
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

    for role in SETUP_ROLES:
        configured = repository.resolve_default(role, workspace_uuid, None)
        effective = configured or built_in_default(role)
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
                "source": "configured_default" if configured is not None else "built_in_fallback",
                "provider_type": effective.get("provider_type"),
                "provider_name": effective.get("provider_name")
                or effective.get("provider_type"),
                "model_name": effective.get("model_name"),
                "model_profile_uuid": effective.get("model_profile_uuid"),
                "endpoint_is_local": bool(effective.get("endpoint_is_local", True)),
                "secret_configured": bool(effective.get("secret_configured", False)),
                "supports_structured_output": bool(
                    effective.get("supports_structured_output", False)
                ),
                "supports_embeddings": bool(effective.get("supports_embeddings", False)),
                "description": spec.description,
                "safe_fallback": spec.safe_beta_default,
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

