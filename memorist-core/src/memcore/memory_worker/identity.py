from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any

from memcore.memory_worker.contracts import PIPELINE_VERSION, PROMPT_BUNDLE_VERSION
from memcore.memory_worker.prompts.versions import (
    JAKOBSON_SENTENCE_ANALYSIS_ACTIVE_VERSION,
    JAKOBSON_SENTENCE_ANALYSIS_PROMPT_ID,
)
from memcore.models import ModelRole
from memcore.repositories.memory_worker import canonical_text_hash


@dataclass(frozen=True)
class ProcessingIdentity:
    target_message_uuid: str
    input_content_hash: str
    pipeline_version: str
    prompt_bundle_version: str
    model_role: str
    model_profile_uuid: str | None
    provider_type: str
    provider_identity: str
    prompt_id: str
    prompt_version: str

    @property
    def identity_hash(self) -> str:
        payload = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_processing_identity(
    *,
    target_message_uuid: str,
    raw_text: str,
    model_target: dict[str, object] | None,
    model_role: str = ModelRole.IMPORT_RECONSTRUCTION.value,
    pipeline_version: str = PIPELINE_VERSION,
    prompt_bundle_version: str = PROMPT_BUNDLE_VERSION,
    prompt_id: str = JAKOBSON_SENTENCE_ANALYSIS_PROMPT_ID,
    prompt_version: str = JAKOBSON_SENTENCE_ANALYSIS_ACTIVE_VERSION,
) -> ProcessingIdentity:
    target = model_target or {}
    provider_type = str(target.get("provider_type") or "deterministic")
    profile_uuid = target.get("model_profile_uuid")
    profile = str(profile_uuid) if profile_uuid else None
    model_name = target.get("model_name") or "deterministic_extraction"
    provider_identity = profile or f"{provider_type}:{model_name}"
    return ProcessingIdentity(
        target_message_uuid=target_message_uuid,
        input_content_hash=canonical_text_hash(raw_text or ""),
        pipeline_version=pipeline_version,
        prompt_bundle_version=prompt_bundle_version,
        model_role=model_role,
        model_profile_uuid=profile,
        provider_type=provider_type,
        provider_identity=provider_identity,
        prompt_id=prompt_id,
        prompt_version=prompt_version,
    )


def execution_profile_fingerprint(target: dict[str, Any] | None) -> str:
    """Hash only execution-relevant, non-secret profile metadata."""

    values = target or {}
    fields = (
        "model_role",
        "role",
        "requested_role",
        "effective_role",
        "scope_source",
        "inheritance_source",
        "fallback_reason",
        "model_profile_uuid",
        "provider_type",
        "provider",
        "model_name",
        "endpoint_url",
        "endpoint_is_local",
        "secret_env_var_name",
        "supports_structured_output",
        "supports_json_mode",
        "supports_embeddings",
        "embedding_dimension",
        "is_enabled",
        "requires_privacy_acknowledgement",
        "privacy_acknowledged_at",
        "prompt_version",
        "schema_version",
        "updated_at",
    )
    material = {field: values.get(field) for field in fields if field in values}
    encoded = json.dumps(
        material,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
