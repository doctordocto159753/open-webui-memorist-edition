from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from typing import Any

from memcore.models import ModelProfile
from memcore.validators.ijson import load_ijson

_SELF_CERTIFYING_PROVIDERS = {"deterministic", "local_embedding"}


def role_contract_hash(role_value: str) -> str | None:
    """Return the complete active role-manifest hash.

    The compatibility name is retained for stored callers; the value now covers
    the actual prompt/version/schema/capability/fallback probe manifest rather
    than incorrectly aliasing import reconstruction to Jakobson.
    """

    from memcore.model_control.role_contracts import role_contract_manifest_hash

    return role_contract_manifest_hash(role_value)


def profile_certification_fingerprint(profile: ModelProfile) -> str:
    """Hash fields that affect provider execution and role compatibility."""

    payload = {
        "role": profile.role.value,
        "provider_type": profile.provider_type,
        "model_name": profile.model_name,
        "endpoint_url": profile.endpoint_url,
        "endpoint_is_local": profile.endpoint_is_local,
        "context_window": profile.context_window,
        "max_input_tokens": profile.max_input_tokens,
        "max_output_tokens": profile.max_output_tokens,
        "supports_structured_output": profile.supports_structured_output,
        "supports_json_mode": profile.supports_json_mode,
        "supports_embeddings": profile.supports_embeddings,
        "embedding_dimension": profile.embedding_dimension,
        "secret_strategy": profile.secret_strategy,
        "secret_env_var_name": profile.secret_env_var_name,
        "is_enabled": profile.is_enabled,
        "role_contract_hash": role_contract_hash(profile.role.value),
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def certification_status(
    profile: ModelProfile,
    event: Mapping[str, Any] | None,
) -> dict[str, Any]:
    reference_configured = bool(
        profile.secret_strategy == "env_var" and profile.secret_env_var_name
    )
    reference_available = not reference_configured or bool(
        os.environ.get(str(profile.secret_env_var_name))
    )
    base = {
        "profile_fingerprint": profile_certification_fingerprint(profile),
        "secret_reference_configured": reference_configured,
        "secret_available_in_core": reference_available,
        "authentication_status": ("not_required" if not reference_configured else "not_validated"),
        "certification_status": "missing",
        "certification_current": False,
        "certified_at": None,
    }
    if (
        profile.provider_type in _SELF_CERTIFYING_PROVIDERS
        and profile.role.value != "memory_extraction"
    ):
        return {
            **base,
            "authentication_status": "not_required",
            "certification_status": "current",
            "certification_current": True,
        }
    if event is None:
        return base

    event_fingerprint = event.get("profile_fingerprint")
    result = _event_result(event)
    compatible = (
        str(event.get("status") or "") == "ok"
        and result.get("overall_status") == "ok"
        and result.get("role_compatibility_status") == "compatible"
        and result.get("role_probe_status") in {"compatible", "not_applicable"}
        and result.get("role_manifest_hash") == role_contract_hash(profile.role.value)
    )
    if event_fingerprint != base["profile_fingerprint"]:
        state = "stale"
    elif compatible:
        state = "current"
    else:
        state = "failed"
    return {
        **base,
        "authentication_status": str(result.get("authentication_status") or "not_validated"),
        "certification_status": state,
        "certification_current": state == "current",
        "certified_at": event.get("created_at"),
    }


def _event_result(event: Mapping[str, Any]) -> dict[str, Any]:
    value = event.get("result_jsonb", event.get("result_ijson"))
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str) and value:
        try:
            decoded = load_ijson(value)
        except (TypeError, ValueError):
            return {}
        return decoded if isinstance(decoded, dict) else {}
    return {}
