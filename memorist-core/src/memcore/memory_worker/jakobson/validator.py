from __future__ import annotations

from typing import Any

from memcore.memory_worker.prompts.registry import PromptValidationError, validate_prompt_output
from memcore.memory_worker.prompts.versions import (
    JAKOBSON_SENTENCE_ANALYSIS_PROMPT_ID,
    JAKOBSON_SENTENCE_ANALYSIS_VERSION,
)


class JakobsonValidationError(ValueError):
    """Raised when a Jakobson provider output violates the schema."""


def validate_jakobson_provider_output(output: dict[str, Any]) -> None:
    try:
        validate_prompt_output(
            JAKOBSON_SENTENCE_ANALYSIS_PROMPT_ID,
            JAKOBSON_SENTENCE_ANALYSIS_VERSION,
            output,
        )
    except PromptValidationError as error:
        raise JakobsonValidationError(str(error)) from error
