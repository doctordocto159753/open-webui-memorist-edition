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
)
from memcore.models import ModelProfile, ModelRole


def provider_for_profile(profile: ModelProfile | dict[str, object] | None) -> ModelProvider:
    if profile is None:
        return DisabledProvider()
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
            )
        if not endpoint_url:
            return DisabledProvider(model_name)
        return OpenAICompatibleLLMProvider(
            str(endpoint_url),
            model_name,
            str(secret_env_var_name) if secret_env_var_name else None,
        )
    if provider_type == "openai_compatible_embedding":
        if not endpoint_url:
            return DisabledProvider(model_name)
        return OpenAICompatibleEmbeddingProvider(
            str(endpoint_url),
            model_name,
            str(secret_env_var_name) if secret_env_var_name else None,
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
        return DisabledProvider(model_name)
    return DisabledProvider(model_name)


def _get(profile: ModelProfile | dict[str, object], key: str) -> object | None:
    if isinstance(profile, dict):
        return profile.get(key)
    return cast(object | None, getattr(profile, key))
