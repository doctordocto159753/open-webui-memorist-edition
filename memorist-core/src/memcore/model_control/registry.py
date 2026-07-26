from __future__ import annotations

from typing import Any, cast

from memcore.model_control.providers import (
    DeterministicProvider,
    DisabledProvider,
    ModelProvider,
    OllamaEmbeddingProvider,
    OllamaLLMProvider,
    OpenAICompatibleEmbeddingProvider,
    ProviderHealth,
    UnavailableProvider,
)
from memcore.model_control.providers.runtime_openai import (
    RuntimeContractOpenAICompatibleLLMProvider,
)
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
        return RuntimeContractOpenAICompatibleLLMProvider(
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
    """Run connectivity plus the actual role contract required by runtime."""

    provider = provider_for_profile(profile)
    health = provider.health_check(timeout_seconds=timeout_seconds)
    if profile is None:
        return health
    role_value = _get(profile, "role")
    role = role_value if isinstance(role_value, ModelRole) else ModelRole(str(role_value))
    from memcore.model_control.role_contracts import role_contract_manifest_hash

    manifest_hash = role_contract_manifest_hash(role)
    if health.overall_status != "ok" or role in {
        ModelRole.MAIN_CHAT_OBSERVED,
        ModelRole.EMBEDDING,
    }:
        return health.model_copy(
            update={
                "role_manifest_hash": manifest_hash,
                "role_probe_status": (
                    "not_applicable"
                    if role is ModelRole.MAIN_CHAT_OBSERVED
                    else ("compatible" if health.overall_status == "ok" else health.overall_status)
                ),
            }
        )
    if role is ModelRole.MEMORY_EXTRACTION:
        return _run_memory_extraction_role_probe(profile, health, manifest_hash, timeout_seconds)
    if str(_get(profile, "provider_type") or "") == "deterministic":
        return health.model_copy(
            update={
                "role_manifest_hash": manifest_hash,
                "role_probe_status": "compatible",
            }
        )
    return _run_structured_role_probe(profile, role, health, manifest_hash, timeout_seconds)


def _run_memory_extraction_role_probe(
    profile: ModelProfile | dict[str, object],
    health: ProviderHealth,
    manifest_hash: str,
    timeout_seconds: float,
) -> ProviderHealth:
    from memcore.memory_worker.jakobson_runtime import execute_jakobson_contract
    from memcore.memory_worker.prompts.contracts import (
        canonical_jakobson_v3_example,
        get_contract,
    )
    from memcore.memory_worker.prompts.versions import (
        JAKOBSON_SENTENCE_ANALYSIS_ACTIVE_VERSION,
        JAKOBSON_SENTENCE_ANALYSIS_PROMPT_ID,
    )

    sentence = "Keep backups enabled."
    payload = {
        "sentences": [
            {
                "id": 1,
                "unit_uuid": "certification-unit-1",
                "message_uuid": "certification-message-1",
                "text": sentence,
                "span_start": 0,
                "span_end": len(sentence),
            }
        ]
    }
    fallback = canonical_jakobson_v3_example()
    fallback["items"][0]["text"] = sentence
    contract = get_contract(
        JAKOBSON_SENTENCE_ANALYSIS_PROMPT_ID,
        JAKOBSON_SENTENCE_ANALYSIS_ACTIVE_VERSION,
    )
    try:
        outcome = execute_jakobson_contract(
            profile=(
                profile.model_dump(mode="json")
                if isinstance(profile, ModelProfile)
                else dict(profile)
            ),
            input_payload=payload,
            deterministic_builder=lambda: fallback,
            timeout_ms=max(1, int(timeout_seconds * 1000)),
            allow_fallback=False,
        )
        if outcome.output.get("status") != "ok" or not outcome.output.get("items"):
            raise ValueError("role probe must return a non-empty ok result")
    except Exception:
        return health.model_copy(
            update={
                "status": "error",
                "overall_status": "incompatible",
                "role_compatibility_status": "incompatible",
                "structured_output_status": "role_contract_invalid",
                "failure_stage": "role_contract_probe",
                "detail_sanitized": (
                    "Provider passed connectivity but failed the active memory-extraction "
                    "prompt contract."
                ),
                "recommended_action": (
                    "Use a model that satisfies the active strict role contract, then retest."
                ),
                "role_manifest_hash": manifest_hash,
                "role_probe_status": "incompatible",
                "role_probe_contract_hash": (
                    contract.contract_hash if contract is not None else None
                ),
            }
        )
    return health.model_copy(
        update={
            "role_manifest_hash": manifest_hash,
            "role_probe_status": "compatible",
            "role_probe_contract_hash": (contract.contract_hash if contract is not None else None),
        }
    )


def _run_structured_role_probe(
    profile: ModelProfile | dict[str, object],
    role: ModelRole,
    health: ProviderHealth,
    manifest_hash: str,
    timeout_seconds: float,
) -> ProviderHealth:
    from memcore.memory_worker.prompts.registry import (
        render_prompt,
        validate_prompt_execution,
    )
    from memcore.model_control.role_contracts import role_contract_manifest
    from memcore.model_control.runtime_contracts import runtime_contract_for_role

    provider = provider_for_profile(profile)
    runtime_contract = runtime_contract_for_role(role)
    if runtime_contract is not None:
        try:
            response = provider.complete_structured(
                runtime_contract.render_probe_prompt(),
                timeout_seconds=timeout_seconds,
            )
            runtime_contract.validate(response.output_ijson)
        except Exception:
            return _failed_role_probe(
                health,
                manifest_hash,
                runtime_contract.contract_hash,
                "Provider passed connectivity but failed the exact runtime stage contract.",
            )
        return health.model_copy(
            update={
                "role_manifest_hash": manifest_hash,
                "role_probe_status": "compatible",
                "role_probe_contract_hash": runtime_contract.contract_hash,
            }
        )

    manifest = role_contract_manifest(role)
    prompt = manifest.get("prompt")
    if not isinstance(prompt, dict):
        return health.model_copy(
            update={
                "role_manifest_hash": manifest_hash,
                "role_probe_status": "incompatible",
            }
        )
    prompt_id = str(prompt["metadata"]["prompt_id"])
    prompt_version = str(prompt["metadata"]["prompt_version"])
    payload = _role_probe_input(role)
    try:
        response = provider.complete_structured(
            render_prompt(prompt_id, prompt_version, payload),
            timeout_seconds=timeout_seconds,
        )
        validate_prompt_execution(prompt_id, prompt_version, payload, response.output_ijson)
        if response.output_ijson.get("status") != "ok" or not response.output_ijson.get("items"):
            raise ValueError("role probe must return a non-empty ok result")
    except Exception:
        return _failed_role_probe(
            health,
            manifest_hash,
            cast(str | None, manifest.get("contract_hash")),
            "Provider passed connectivity but failed the active role contract.",
        )
    return health.model_copy(
        update={
            "role_manifest_hash": manifest_hash,
            "role_probe_status": "compatible",
            "role_probe_contract_hash": manifest.get("contract_hash"),
        }
    )


def _failed_role_probe(
    health: ProviderHealth,
    manifest_hash: str,
    contract_hash: str | None,
    detail: str,
) -> ProviderHealth:
    return health.model_copy(
        update={
            "status": "error",
            "overall_status": "incompatible",
            "role_compatibility_status": "incompatible",
            "structured_output_status": "role_contract_invalid",
            "failure_stage": "role_contract_probe",
            "detail_sanitized": detail,
            "recommended_action": (
                "Use a model that satisfies the active role contract, then retest."
            ),
            "role_manifest_hash": manifest_hash,
            "role_probe_status": "incompatible",
            "role_probe_contract_hash": contract_hash,
        }
    )


def _role_probe_input(role: ModelRole) -> dict[str, Any]:
    fixtures: dict[ModelRole, dict[str, Any]] = {
        ModelRole.PREFLIGHT: {
            "current_user_message": "Remember that backups stay enabled.",
            "main_chat_model": "certification-model",
            "attachment_budget": {"max_tokens": 64},
            "active_blocks": [],
            "retrieval_candidates": [],
            "conflicts": [],
            "security_warnings": [],
        },
        ModelRole.IMPORT_RECONSTRUCTION: {
            "import_run_uuid": "certification-import",
            "provider": "certification",
            "conversation": {"title": "Contract probe"},
            "messages": [],
            "known_schema_fields": [],
            "unknown_fields": [],
        },
    }
    return fixtures[role]


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
        dimension = int(cast(Any, value))
    except (TypeError, ValueError):
        return None
    return dimension if dimension > 0 else None
