from typing import Any

from pydantic import ValidationError

from memcore.memory_worker.analysis.schemas import AnalysisResponse


class AnalysisValidationError(ValueError):
    pass


def validate_analysis_output(value: Any, source_text: str) -> AnalysisResponse:
    try:
        response = (
            value if isinstance(value, AnalysisResponse) else AnalysisResponse.model_validate(value)
        )
    except ValidationError as error:
        raise AnalysisValidationError(str(error)) from error

    for nucleus in response.conceptual_nuclei:
        span = nucleus.get("source_span")
        if span is None:
            continue
        start = int(span.get("start", -1))
        end = int(span.get("end", -1))
        text = str(span.get("text", ""))
        if start < 0 or end <= start or source_text[start:end] != text:
            raise AnalysisValidationError(
                "conceptual nucleus source_span does not match source text"
            )
    return response
