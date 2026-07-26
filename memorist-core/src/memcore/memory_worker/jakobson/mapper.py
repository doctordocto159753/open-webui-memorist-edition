from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypedDict

from memcore.memory_worker.jakobson.repair import compare_sentence_text
from memcore.memory_worker.prompts.contracts import canonical_sentence_items
from memcore.models import (
    JakobsonConfidence,
    JakobsonFunction,
    JakobsonSentenceAnnotation,
    TextUnit,
)
from memcore.repositories.memory_worker import canonical_text_hash
from memcore.validators.ijson import dump_ijson

FACTOR_FIELD_MAP = {
    "sender_addresser": "sender",
    "receiver_addressee": "receiver",
    "message": "message",
    "context_referent": "context",
    "code": "code",
    "contact_channel": "contact_channel",
}


class FactorValues(TypedDict):
    value: str | None
    evidence: str | None
    confidence: str


def map_output_to_annotations(
    *,
    analysis_run_uuid: str,
    message_uuid: str,
    units: list[TextUnit],
    output: Mapping[str, Any],
) -> tuple[list[JakobsonSentenceAnnotation], list[str]]:
    sentences = canonical_sentence_items(output)
    if not isinstance(sentences, list):
        raise ValueError("Jakobson output sentences must be a list")
    if len(sentences) != len(units):
        raise ValueError("Jakobson sentence count does not match deterministic units")

    warnings: list[str] = []
    annotations: list[JakobsonSentenceAnnotation] = []
    for index, sentence in enumerate(sentences):
        if not isinstance(sentence, Mapping):
            raise ValueError("Jakobson sentence entry must be an object")
        unit = units[index]
        sentence_text = str(sentence["text"])
        warning = compare_sentence_text(sentence_text, unit.text)
        if warning:
            warnings.append(f"sentence {index + 1}: {warning}")
        factors = sentence["six_factors"]
        if not isinstance(factors, Mapping):
            raise ValueError("six_factors must be an object")

        factor_values = {
            local_name: _factor_values(factors[prompt_name])
            for prompt_name, local_name in FACTOR_FIELD_MAP.items()
        }
        annotations.append(
            JakobsonSentenceAnnotation(
                analysis_run_uuid=analysis_run_uuid,
                message_uuid=message_uuid,
                unit_uuid=unit.text_unit_uuid,
                sentence_index=index + 1,
                sentence_text=unit.text,
                sentence_hash=canonical_text_hash(unit.text),
                sender_value=factor_values["sender"]["value"],
                sender_evidence=factor_values["sender"]["evidence"],
                sender_confidence=JakobsonConfidence(factor_values["sender"]["confidence"]),
                receiver_value=factor_values["receiver"]["value"],
                receiver_evidence=factor_values["receiver"]["evidence"],
                receiver_confidence=JakobsonConfidence(factor_values["receiver"]["confidence"]),
                message_value=factor_values["message"]["value"],
                message_evidence=factor_values["message"]["evidence"],
                message_confidence=JakobsonConfidence(factor_values["message"]["confidence"]),
                context_value=factor_values["context"]["value"],
                context_evidence=factor_values["context"]["evidence"],
                context_confidence=JakobsonConfidence(factor_values["context"]["confidence"]),
                code_value=factor_values["code"]["value"],
                code_evidence=factor_values["code"]["evidence"],
                code_confidence=JakobsonConfidence(factor_values["code"]["confidence"]),
                contact_channel_value=factor_values["contact_channel"]["value"],
                contact_channel_evidence=factor_values["contact_channel"]["evidence"],
                contact_channel_confidence=JakobsonConfidence(
                    factor_values["contact_channel"]["confidence"]
                ),
                dominant_function=JakobsonFunction(str(sentence["dominant_function"])),
                secondary_functions_ijson=dump_ijson(sentence["secondary_functions"]),
                function_reason=str(sentence.get("function_reason") or ""),
                notes=str(sentence.get("notes") or ""),
                raw_sentence_output_ijson=dump_ijson(dict(sentence)),
            )
        )
    return annotations, warnings


def _factor_values(value: Any) -> FactorValues:
    if not isinstance(value, Mapping):
        raise ValueError("Jakobson factor must be an object")
    return {
        "value": None if value["value"] is None else str(value["value"]),
        "evidence": None if value["evidence"] is None else str(value["evidence"]),
        "confidence": str(value["confidence"]),
    }
