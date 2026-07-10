from __future__ import annotations

from typing import cast

from memcore.model_control.providers import (
    DeterministicProvider,
    DisabledProvider,
    ModelProvider,
    OllamaEmbeddingProvider,
    OllamaLLMProvider,
    OpenAICompatibleEmbeddingProvider,
    OpenAICompatibleLLMProvider,
    UnavailableProvider,
)
from memcore.model_control.providers import ProviderHealth
from memcore.models import ModelProfile, ModelRole


def provider_for_profile(profile: ModelProfile | dict[str, object] | None) -> ModelProvider:
    if profile is None:
        return DisabledProvider()
    if _get(profile, "is_enabled") is False:
        return DisabledProvider(str(_get(profile, "model_name") or "disabled"))
    provider_type = _get(profile, "provider_type") or _get(profile, "provider") or "disabled"
    model_name = str(_get(profile, "model_name") or "disabled")
    endpoint_url = _get(profile, "endpoint_url")
    secret_env_var_name = _get(profile, "secret_env_var_name")

    role = _get(profile, "role")
    role_text = role.value if isinstance(role, ModelRole) else str(role or "")

    if provider_type in {"deterministic", "local_embedding"}:
        return DeterministicProvider(model_name)
    if provider_type in {"openai_compatible", "openai_compatible_llm"}:
        if role_text == ModelRole.EMBEDDING.value:
            if not endpoint_url:
                return DisabledProvider(model_name)
            return OpenAICompatibleEmbeddingProvider(
                str(endpoint_url),
                model_name,
                str(secret_env_var_name) if secret_env_var_name else None,
                supports_json_mode=bool(_get(profile, "supports_json_mode")),
                supports_structured_output=bool(_get(profile, "supports_structured_output")),
                embedding_dimension=_embedding_dimension(profile),
            )
        if not endpoint_url:
            return DisabledProvider(model_name)
        return OpenAICompatibleLLMProvider(
            str(endpoint_url),
            model_name,
            str(secret_env_var_name) if secret_env_var_name else None,
            supports_json_mode=bool(_get(profile, "supports_json_mode")),
            supports_structured_output=bool(_get(profile, "supports_structured_output")),
            requires_structured_extraction=_role_requires_structured_extraction(role_text),
        )
    if provider_type == "openai_compatible_embedding":
        if not endpoint_url:
            return DisabledProvider(model_name)
        return OpenAICompatibleEmbeddingProvider(
            str(endpoint_url),
            model_name,
            str(secret_env_var_name) if secret_env_var_name else None,
            supports_json_mode=bool(_get(profile, "supports_json_mode")),
            supports_structured_output=bool(_get(profile, "supports_structured_output")),
            embedding_dimension=_embedding_dimension(profile),
        )
    if provider_type in {"ollama", "ollama_llm"}:
        if not endpoint_url:
            return DisabledProvider(model_name)
        return OllamaLLMProvider(str(endpoint_url), model_name)
    if provider_type == "ollama_embedding":
        if not endpoint_url:
            return DisabledProvider(model_name)
        return OllamaEmbeddingProvider(str(endpoint_url), model_name)
    if provider_type == "unknown":
        return UnavailableProvider(model_name, str(provider_type), "unknown provider type")
    return UnavailableProvider(
        model_name,
        str(provider_type),
        f"unknown provider type: {provider_type}",
    )


def test_profile_health(
    profile: ModelProfile | dict[str, object] | None,
    timeout_seconds: float = 1.0,
) -> ProviderHealth:
    """Run the profile-appropriate health test.

    LLM-oriented roles use chat completions, embedding roles and explicit embedding
    providers use embeddings, deterministic profiles return local success, disabled
    profiles return disabled, and unknown provider types return an error.
    """
    provider = provider_for_profile(profile)
    return provider.health_check(timeout_seconds=timeout_seconds)


def _get(profile: ModelProfile | dict[str, object], key: str) -> object | None:
    if isinstance(profile, dict):
        return profile.get(key)
    return cast(object | None, getattr(profile, key))


def _role_requires_structured_extraction(role_text: str) -> bool:
    return role_text in {
        ModelRole.PREFLIGHT.value,
        ModelRole.MEMORY_EXTRACTION.value,
        ModelRole.HIGH_CONFIDENCE_EXTRACTION.value,
        ModelRole.IMPORT_RECONSTRUCTION.value,
        ModelRole.BLOCK_COMPACTION.value,
        ModelRole.PRIVACY_SENSITIVITY.value,
    }


def _embedding_dimension(profile: ModelProfile | dict[str, object]) -> int | None:
    value = _get(profile, "embedding_dimension")
    if value is None:
        return None
    try:
        dimension = int(value)
    except (TypeError, ValueError):
        return None
    return dimension if dimension > 0 else None
