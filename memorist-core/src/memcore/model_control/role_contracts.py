"""Authoritative role-to-runtime contract manifest used by certification."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from memcore.memory_worker.prompts.contracts import get_contract
from memcore.memory_worker.prompts.registry import get_prompt
from memcore.memory_worker.prompts.versions import (
    JAKOBSON_SENTENCE_ANALYSIS_ACTIVE_VERSION,
    JAKOBSON_SENTENCE_ANALYSIS_PROMPT_ID,
)
from memcore.model_control.runtime_contracts import runtime_contract_for_role
from memcore.models import ModelRole

ROLE_CONTRACT_MANIFEST_VERSION = "role-contract-manifest-v2"

_ROLE_PROMPTS: dict[ModelRole, tuple[str, str] | None] = {
    ModelRole.MAIN_CHAT_OBSERVED: None,
    ModelRole.MEMORY_EXTRACTION: (
        JAKOBSON_SENTENCE_ANALYSIS_PROMPT_ID,
        JAKOBSON_SENTENCE_ANALYSIS_ACTIVE_VERSION,
    ),
    ModelRole.PREFLIGHT: ("memorist.preflight_planning", "2.0"),
    ModelRole.EMBEDDING: None,
    ModelRole.IMPORT_RECONSTRUCTION: ("memorist.import_reconstruction", "2.0"),
    ModelRole.HIGH_CONFIDENCE_EXTRACTION: None,
    ModelRole.BLOCK_COMPACTION: None,
    ModelRole.PRIVACY_SENSITIVITY: None,
}


def role_contract_manifest(role: ModelRole | str) -> dict[str, Any]:
    resolved = role if isinstance(role, ModelRole) else ModelRole(role)
    if resolved is ModelRole.MAIN_CHAT_OBSERVED:
        return {
            "manifest_version": ROLE_CONTRACT_MANIFEST_VERSION,
            "role": resolved.value,
            "certifiable": False,
            "execution": "observed_by_openwebui",
        }
    if resolved is ModelRole.EMBEDDING:
        return {
            "manifest_version": ROLE_CONTRACT_MANIFEST_VERSION,
            "role": resolved.value,
            "certifiable": True,
            "execution": "embedding_vector",
            "required_capabilities": ["embeddings"],
            "fallback_policy": "fail_open",
        }

    runtime_contract = runtime_contract_for_role(resolved)
    if runtime_contract is not None:
        return {
            "manifest_version": ROLE_CONTRACT_MANIFEST_VERSION,
            "role": resolved.value,
            "certifiable": True,
            "execution": "stage_invoker",
            "runtime_contract": runtime_contract.manifest(),
            "contract_hash": runtime_contract.contract_hash,
            "required_capabilities": ["structured_output_or_json_mode"],
            "fallback_policy": "fail_open",
            "certification_probe_version": "runtime-role-probe-v2",
        }

    prompt_ref = _ROLE_PROMPTS[resolved]
    assert prompt_ref is not None
    prompt_id, prompt_version = prompt_ref
    prompt = get_prompt(prompt_id, prompt_version)
    typed_contract = get_contract(prompt_id, prompt_version)
    return {
        "manifest_version": ROLE_CONTRACT_MANIFEST_VERSION,
        "role": resolved.value,
        "certifiable": True,
        "execution": "structured_prompt",
        "prompt": prompt.model_dump(mode="json"),
        "contract_hash": (typed_contract.contract_hash if typed_contract is not None else None),
        "required_capabilities": ["structured_output_or_json_mode"],
        "fallback_policy": "fail_open",
        "certification_probe_version": "role-probe-v1",
    }


def role_contract_manifest_hash(role: ModelRole | str) -> str:
    encoded = json.dumps(
        role_contract_manifest(role),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def list_role_contract_manifests() -> list[dict[str, Any]]:
    return [role_contract_manifest(role) for role in ModelRole]
