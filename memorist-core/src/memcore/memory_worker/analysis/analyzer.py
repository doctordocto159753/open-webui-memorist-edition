from typing import Any

from memcore.memory_worker.analysis.prompts import ANALYSIS_PROMPT_VERSION
from memcore.memory_worker.analysis.provider import (
    DeterministicStructuredAnalysisProvider,
    StructuredAnalysisProvider,
)
from memcore.memory_worker.analysis.schemas import (
    RESPONSE_SCHEMA,
    AnalysisRequest,
    AnalysisResponse,
)
from memcore.memory_worker.analysis.validation import (
    AnalysisValidationError,
    validate_analysis_output,
)
from memcore.models import LinguisticAnalysis, TextUnit
from memcore.validators.ijson import dump_ijson


class StructuredAnalyzer:
    def __init__(self, provider: StructuredAnalysisProvider | None = None) -> None:
        self.provider = provider or DeterministicStructuredAnalysisProvider()

    def analyze(
        self,
        unit: TextUnit,
        processing_run_uuid: str,
        source_timestamp: str | None = None,
        session_uuid: str | None = None,
        project_uuid: str | None = None,
    ) -> LinguisticAnalysis:
        request = AnalysisRequest(
            text_unit_uuid=unit.text_unit_uuid,
            text=unit.text,
            speaker_role=unit.speaker_role,
            source_timestamp=source_timestamp,
            session_uuid=session_uuid,
            project_uuid=project_uuid,
        )
        response, validation_errors = self._call_with_one_repair(request)
        return LinguisticAnalysis(
            text_unit_uuid=unit.text_unit_uuid,
            processing_run_uuid=processing_run_uuid,
            analysis_schema_version=ANALYSIS_PROMPT_VERSION,
            speech_acts_ijson=dump_ijson(response.speech_acts),
            jakobson_functions_ijson=dump_ijson(response.jakobson_functions),
            conceptual_nuclei_ijson=dump_ijson(response.conceptual_nuclei),
            entity_mentions_ijson=dump_ijson(response.entity_mentions),
            temporal_expressions_ijson=dump_ijson(response.temporal_expressions),
            modality_ijson=dump_ijson(response.modality),
            memory_signals_ijson=dump_ijson(response.memory_signals),
            abstention_ijson=dump_ijson(response.abstention),
            validation_errors_ijson=dump_ijson(validation_errors) if validation_errors else None,
        )

    def _call_with_one_repair(
        self,
        request: AnalysisRequest,
    ) -> tuple[AnalysisResponse, list[str]]:
        errors: list[str] = []
        for attempt in range(2):
            try:
                raw: Any = self.provider.analyze(request, RESPONSE_SCHEMA)
                return validate_analysis_output(raw, request.text), errors
            except AnalysisValidationError as error:
                errors.append(str(error))
                if attempt == 1:
                    raise
        raise AssertionError("unreachable analysis repair loop")
