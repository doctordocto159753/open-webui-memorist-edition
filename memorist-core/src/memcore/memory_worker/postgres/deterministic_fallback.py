from __future__ import annotations

from typing import Any

from memcore.memory_worker.prompts.versions import (
    JAKOBSON_SENTENCE_ANALYSIS_PROMPT_ID,
    PROMPT_PACK_VERSION,
)
from memcore.memory_worker.semantic import SemanticFactorMatch, resolve_semantic_factors
from memcore.models import JakobsonFunction


def deterministic_jakobson_output(_self: object, units: list[dict[str, Any]]) -> dict[str, Any]:
    """Build Full/PostgreSQL deterministic Jakobson output via the shared factor resolver.

    This keeps the Full fallback payload shape compatible with the existing
    PostgreSQL pipeline while removing its separate receiver/context defaults.
    Later PR4-D tasks can replace this compatibility adapter with a direct
    canonical decision object in the pipeline.
    """

    sentences: list[dict[str, Any]] = []
    for idx, unit in enumerate(units, start=1):
        text = str(unit["text"])
        resolved = resolve_semantic_factors(
            text,
            dominant_function=JakobsonFunction.REFERENTIAL,
        )
        receiver = _factor_payload(
            resolved.receiver,
            fallback_value="assistant",
            fallback_evidence=text,
        )
        context = _factor_payload(
            resolved.context,
            fallback_value=text,
            fallback_evidence=text,
        )
        sentences.append(
            {
                "id": idx,
                "text": text,
                "six_factors": {
                    "sender_addresser": {
                        "value": "user",
                        "evidence": text,
                        "confidence": "medium",
                    },
                    "receiver_addressee": receiver,
                    "message": {"value": text, "evidence": text, "confidence": "high"},
                    "context_referent": context,
                    "code": {
                        "value": "natural language",
                        "evidence": text,
                        "confidence": "medium",
                    },
                    "contact_channel": {
                        "value": "chat",
                        "evidence": text,
                        "confidence": "medium",
                    },
                },
                "dominant_function": JakobsonFunction.REFERENTIAL.value,
                "secondary_functions": [],
                "function_reason": "Deterministic fallback classifies the sentence as referential.",
                "notes": "deterministic fallback",
            }
        )

    return {
        "schema_version": "1.0",
        "prompt_id": JAKOBSON_SENTENCE_ANALYSIS_PROMPT_ID,
        "prompt_version": PROMPT_PACK_VERSION,
        "status": "ok",
        "warnings": ["deterministic_fallback"],
        "items": [],
        "analysis_level": "sentence",
        "model": "jakobson_six_factor",
        "input_language": "mixed",
        "sentence_count": len(sentences),
        "sentences": sentences,
        "overall_summary": {
            "dominant_overall_function": JakobsonFunction.REFERENTIAL.value,
            "secondary_overall_functions": [],
            "main_sender": "user",
            "main_receiver": _overall_factor_value(
                sentences, "receiver_addressee", fallback="assistant"
            ),
            "main_context": _overall_factor_value(
                sentences, "context_referent", fallback="conversation"
            ),
            "main_code": "natural language",
            "main_contact_channel": "chat",
        },
    }


def _factor_payload(
    match: SemanticFactorMatch,
    *,
    fallback_value: str,
    fallback_evidence: str,
) -> dict[str, str]:
    return {
        "value": match.value or fallback_value,
        "evidence": match.evidence or fallback_evidence,
        "confidence": match.confidence,
    }


def _overall_factor_value(sentences: list[dict[str, Any]], key: str, *, fallback: str) -> str:
    if not sentences:
        return fallback
    value = sentences[0].get("six_factors", {}).get(key, {}).get("value")
    return str(value or fallback)
