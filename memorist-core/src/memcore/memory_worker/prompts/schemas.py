from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from memcore.memory_worker.prompts.versions import (
    JAKOBSON_SENTENCE_ANALYSIS_PROMPT_ID,
    JAKOBSON_SENTENCE_ANALYSIS_VERSION,
)

ALLOWED_JAKOBSON_FUNCTIONS = {
    "referential",
    "emotive",
    "conative",
    "phatic",
    "metalingual",
    "poetic",
}
ALLOWED_CONFIDENCE_VALUES = {"high", "medium", "low"}
JAKOBSON_FACTOR_KEYS = {
    "sender_addresser",
    "receiver_addressee",
    "message",
    "context_referent",
    "code",
    "contact_channel",
}


def valid_jakobson_output() -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "prompt_id": JAKOBSON_SENTENCE_ANALYSIS_PROMPT_ID,
        "prompt_version": JAKOBSON_SENTENCE_ANALYSIS_VERSION,
        "status": "ok",
        "warnings": [],
        "items": [],
        "analysis_level": "sentence",
        "model": "jakobson_six_factor",
        "input_language": "mixed",
        "sentence_count": 1,
        "sentences": [
            {
                "id": 1,
                "text": "Use Jira for workflow tracking.",
                "six_factors": {
                    "sender_addresser": _factor("user", "Use Jira", "high"),
                    "receiver_addressee": _factor("AI or project team", "Use", "medium"),
                    "message": _factor("Use Jira for workflow tracking.", "Use Jira", "high"),
                    "context_referent": _factor("workflow tracking", "workflow tracking", "high"),
                    "code": _factor("English; project workflow terminology", "Jira", "high"),
                    "contact_channel": _factor("chat", "sentence is in chat input", "medium"),
                },
                "dominant_function": "conative",
                "secondary_functions": ["referential"],
                "function_reason": "The sentence instructs the receiver to use Jira.",
                "notes": "",
            }
        ],
        "overall_summary": {
            "dominant_overall_function": "conative",
            "secondary_overall_functions": ["referential"],
            "main_sender": "user",
            "main_receiver": "AI or project team",
            "main_context": "workflow tracking",
            "main_code": "English; project workflow terminology",
            "main_contact_channel": "chat",
        },
    }


def _factor(value: str, evidence: str, confidence: str) -> dict[str, str]:
    return {"value": value, "evidence": evidence, "confidence": confidence}


def validate_jakobson_output(output: Mapping[str, Any]) -> None:
    required = {
        "analysis_level",
        "model",
        "input_language",
        "sentence_count",
        "sentences",
        "overall_summary",
    }
    missing = sorted(required - set(output))
    if missing:
        raise ValueError(f"Jakobson output missing required fields: {missing}")
    if output["analysis_level"] != "sentence":
        raise ValueError("analysis_level must be sentence")
    if output["model"] != "jakobson_six_factor":
        raise ValueError("model must be jakobson_six_factor")
    sentences = output["sentences"]
    if not isinstance(sentences, list):
        raise ValueError("sentences must be a list")
    if output["sentence_count"] != len(sentences):
        raise ValueError("sentence_count must match sentences length")
    if not isinstance(output["overall_summary"], Mapping):
        raise ValueError("overall_summary must be an object")

    for sentence in sentences:
        _validate_sentence(sentence)
    _validate_overall(output["overall_summary"])


def _validate_sentence(sentence: Any) -> None:
    if not isinstance(sentence, Mapping):
        raise ValueError("sentences entries must be objects")
    required = {
        "id",
        "text",
        "six_factors",
        "dominant_function",
        "secondary_functions",
        "function_reason",
        "notes",
    }
    missing = sorted(required - set(sentence))
    if missing:
        raise ValueError(f"Jakobson sentence missing required fields: {missing}")
    if not isinstance(sentence["id"], int) or sentence["id"] < 1:
        raise ValueError("sentence id must be a positive integer")
    if not isinstance(sentence["text"], str) or not sentence["text"]:
        raise ValueError("sentence text must be a non-empty string")
    factors = sentence["six_factors"]
    if not isinstance(factors, Mapping):
        raise ValueError("six_factors must be an object")
    missing_factors = sorted(JAKOBSON_FACTOR_KEYS - set(factors))
    if missing_factors:
        raise ValueError(f"missing six factor fields: {missing_factors}")
    for factor_name in JAKOBSON_FACTOR_KEYS:
        _validate_factor(factor_name, factors[factor_name])
    _validate_function(sentence["dominant_function"], "dominant_function")
    secondary = sentence["secondary_functions"]
    if not isinstance(secondary, list):
        raise ValueError("secondary_functions must be a list")
    for item in secondary:
        _validate_function(item, "secondary_functions")
    if not isinstance(sentence["function_reason"], str):
        raise ValueError("function_reason must be a string")
    if sentence["notes"] is not None and not isinstance(sentence["notes"], str):
        raise ValueError("notes must be a string or null")


def _validate_factor(name: str, factor: Any) -> None:
    if not isinstance(factor, Mapping):
        raise ValueError(f"{name} must be an object")
    missing = sorted({"value", "evidence", "confidence"} - set(factor))
    if missing:
        raise ValueError(f"{name} missing required fields: {missing}")
    if factor["value"] is not None and not isinstance(factor["value"], str):
        raise ValueError(f"{name}.value must be a string or null")
    if factor["evidence"] is not None and not isinstance(factor["evidence"], str):
        raise ValueError(f"{name}.evidence must be a string or null")
    if factor["confidence"] not in ALLOWED_CONFIDENCE_VALUES:
        raise ValueError(f"{name}.confidence is invalid")


def _validate_function(value: Any, field_name: str) -> None:
    if value not in ALLOWED_JAKOBSON_FUNCTIONS:
        raise ValueError(f"{field_name} has invalid function value: {value}")


def _validate_overall(summary: Mapping[str, Any]) -> None:
    required = {
        "dominant_overall_function",
        "secondary_overall_functions",
        "main_sender",
        "main_receiver",
        "main_context",
        "main_code",
        "main_contact_channel",
    }
    missing = sorted(required - set(summary))
    if missing:
        raise ValueError(f"overall_summary missing required fields: {missing}")
    _validate_function(summary["dominant_overall_function"], "dominant_overall_function")
    secondary = summary["secondary_overall_functions"]
    if not isinstance(secondary, list):
        raise ValueError("secondary_overall_functions must be a list")
    for item in secondary:
        _validate_function(item, "secondary_overall_functions")
