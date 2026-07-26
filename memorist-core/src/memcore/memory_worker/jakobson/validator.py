from __future__ import annotations

from typing import Any

from memcore.memory_worker.prompts.registry import PromptValidationError, validate_prompt_output
from memcore.memory_worker.prompts.versions import (
    JAKOBSON_SENTENCE_ANALYSIS_ACTIVE_VERSION,
    JAKOBSON_SENTENCE_ANALYSIS_PROMPT_ID,
)


class JakobsonValidationError(ValueError):
    """Raised when a Jakobson provider output violates the schema."""


def validate_jakobson_provider_output(output: dict[str, Any]) -> None:
    # Validate against the version the output itself declares so both the active
    # v3 contract and any historical v2 output validate under their own schema.
    version = str(output.get("prompt_version") or JAKOBSON_SENTENCE_ANALYSIS_ACTIVE_VERSION)
    try:
        validate_prompt_output(
            JAKOBSON_SENTENCE_ANALYSIS_PROMPT_ID,
            version,
            output,
        )
    except PromptValidationError as error:
        raise JakobsonValidationError(str(error)) from error
