import sqlite3
from time import perf_counter

from memcore.config import Settings
from memcore.memory_worker.analysis.analyzer import StructuredAnalyzer
from memcore.memory_worker.consolidation.consolidator import MemoryConsolidator
from memcore.memory_worker.contracts import (
    JOB_CANDIDATE_EXTRACTION,
    JOB_GRAPH_PROJECTION,
    JOB_HIGH_CONFIDENCE_EXTRACTION,
    JOB_MEMORY_ANALYSIS,
    JOB_MEMORY_CONSOLIDATION,
    JOB_MEMORY_GATING,
)
from memcore.memory_worker.extraction.extractor import CandidateExtractor
from memcore.memory_worker.gating import DeterministicGate
from memcore.memory_worker.graph import GraphProjectionRunner
from memcore.memory_worker.identity import build_processing_identity
from memcore.memory_worker.jakobson.service import (
    DeterministicJakobsonProvider,
    JakobsonAnalysisService,
    OpenAICompatibleJakobsonProvider,
)
from memcore.memory_worker.segmentation.sentence_segmenter import SentenceSegmenter
from memcore.model_control.repository import ModelControlRepository
from memcore.model_control.schemas import UsageEventCreate
from memcore.models import (
    GateDecisionValue,
    LinguisticAnalysis,
    MemoryCandidate,
    MemoryGateDecision,
    Message,
    ModelRole,
    ProcessingRunStatus,
    ProcessingStatus,
    TextUnit,
)
from memcore.repositories import JobRepository, MessageRepository, RepositoryError
from memcore.repositories.memory_worker import (
    GateDecisionRepository,
    LinguisticAnalysisRepository,
    MemoryCandidateRepository,
    MemoryProcessingRunRepository,
    TextUnitRepository,
)


class MemoryWorkerPipeline:
    def __init__(self, connection: sqlite3.Connection, settings: Settings) -> None:
        self.connection = connection
        self.settings = settings
        self.messages = MessageRepository(connection)
        self.jobs = JobRepository(connection)
        self.runs = MemoryProcessingRunRepository(connection)
        self.units = TextUnitRepository(connection)
        self.gates = GateDecisionRepository(connection)
        self.analyses = LinguisticAnalysisRepository(connection)
        self.candidates = MemoryCandidateRepository(connection)
        self.unitizer = SentenceSegmenter()
        self.gate = DeterministicGate()
        self.analyzer = StructuredAnalyzer()
        self.extractor = CandidateExtractor()
        self.consolidator = MemoryConsolidator(connection)
        self.jakobson = JakobsonAnalysisService(connection, segmenter=self.unitizer)

    def process_message(
        self,
        message_uuid: str,
        import_run_uuid: str | None = None,
        job_uuid: str | None = None,
        model_target: dict[str, object] | None = None,
    ) -> dict[str, object]:
        message = self.messages.get_message(message_uuid)
        if message is None:
            raise RepositoryError(f"Message not found: {message_uuid}")
        raw_text = message.raw_text or ""
        extraction_profile = self._resolve_memory_extraction_profile(model_target)
        identity = build_processing_identity(
            target_message_uuid=message.message_uuid,
            raw_text=raw_text,
            model_target=extraction_profile,
            model_role=str(
                extraction_profile.get("model_role") or ModelRole.MEMORY_EXTRACTION.value
            ),
        )
        run = self.runs.get_or_create_run(
            session_uuid=message.session_uuid,
            message_uuid=message.message_uuid,
            pipeline_version=identity.pipeline_version,
            prompt_bundle_version=identity.prompt_bundle_version,
            input_content_hash=identity.input_content_hash,
            prompt_id=identity.prompt_id,
            prompt_version=identity.prompt_version,
            model_profile_uuid=identity.model_profile_uuid,
            provider_type=identity.provider_type,
            model_role=identity.model_role,
            model_name=str(extraction_profile.get("model_name") or "deterministic_extraction"),
            processing_identity_hash=identity.identity_hash,
            input_hash=identity.input_content_hash,
        )
        if run.status is ProcessingRunStatus.SUCCEEDED:
            return self._completed_result(run.processing_run_uuid, message_uuid)
        self.runs.mark_started(run.processing_run_uuid)

        units = self._unitize(message, raw_text)
        self.messages.mark_processing_status(message_uuid, ProcessingStatus.UNITIZED)

        extraction_profile_uuid = _optional_string(extraction_profile.get("model_profile_uuid"))
        if extraction_profile["provider_type"] == "disabled":
            ModelControlRepository(self.connection).record_usage_event(
                UsageEventCreate(
                    role=ModelRole.MEMORY_EXTRACTION,
                    stage="memory_extraction_disabled",
                    model_profile_uuid=extraction_profile_uuid,
                    session_uuid=message.session_uuid,
                    message_uuid=message.message_uuid,
                    import_run_uuid=import_run_uuid,
                    job_uuid=job_uuid,
                    status="disabled",
                )
            )
            self.messages.mark_processing_status(message_uuid, ProcessingStatus.AVAILABLE)
            return {
                "processing_run_uuid": run.processing_run_uuid,
                "message_uuid": message_uuid,
                "units": len(units),
                "jakobson_annotations": 0,
                "memory_signal_routes": 0,
                "gate_decisions": 0,
                "analyses": 0,
                "candidates": 0,
                "consolidation_decisions": 0,
                "graph_projection": {"status": "skipped"},
                "model_role": str(
                    extraction_profile.get("model_role") or ModelRole.MEMORY_EXTRACTION.value
                ),
                "model_profile_uuid": extraction_profile_uuid,
            }

        provider_type = str(extraction_profile["provider_type"])
        if import_run_uuid is not None and provider_type in {
            "openai_compatible",
            "openai_compatible_llm",
        }:
            profile_uuid = extraction_profile.get("model_profile_uuid")
            stored_profile = (
                ModelControlRepository(self.connection).get_profile(str(profile_uuid))
                if profile_uuid
                else None
            )
            if stored_profile is None:
                raise RepositoryError("configured memory extraction profile was not found")
            self.jakobson.provider = OpenAICompatibleJakobsonProvider(
                stored_profile.model_dump(mode="json")
            )
        else:
            self.jakobson.provider = DeterministicJakobsonProvider(
                provider_type=provider_type,
                model_name=str(extraction_profile["model_name"]),
            )
        self.jakobson.model_profile_uuid = extraction_profile_uuid
        self.jakobson.model_role = ModelRole(identity.model_role)
        jakobson_started = perf_counter()
        jakobson_result = self.jakobson.run_for_message(
            message_uuid,
            import_run_uuid=import_run_uuid,
            job_uuid=job_uuid,
        )
        ModelControlRepository(self.connection).record_usage_event(
            UsageEventCreate(
                role=ModelRole(
                    str(extraction_profile.get("model_role") or ModelRole.MEMORY_EXTRACTION.value)
                ),
                stage="jakobson_sentence_analysis",
                model_profile_uuid=extraction_profile_uuid,
                session_uuid=message.session_uuid,
                message_uuid=message.message_uuid,
                import_run_uuid=import_run_uuid,
                job_uuid=job_uuid,
                input_tokens=int(
                    getattr(self.jakobson.provider, "input_tokens", 0)
                    or max(0, (len(raw_text) + 3) // 4)
                ),
                output_tokens=int(
                    getattr(self.jakobson.provider, "output_tokens", 0)
                    or jakobson_result["annotations"]
                ),
                latency_ms=int(
                    getattr(self.jakobson.provider, "latency_ms", 0)
                    or (perf_counter() - jakobson_started) * 1000
                ),
                status="ok",
            )
        )

        decisions = self._gate_units(run.processing_run_uuid, units)
        self.messages.mark_processing_status(message_uuid, ProcessingStatus.GATED)

        analyses = self._analyze_units(message, run.processing_run_uuid, units, decisions)
        if analyses:
            self.messages.mark_processing_status(message_uuid, ProcessingStatus.ANALYZED)

        candidates = self._extract_candidates(message, run.processing_run_uuid, units)
        self.messages.mark_processing_status(message_uuid, ProcessingStatus.CANDIDATES_CREATED)

        decisions_created = [
            self.consolidator.consolidate_candidate(candidate.candidate_uuid)
            for candidate in candidates
        ]
        self.messages.mark_processing_status(message_uuid, ProcessingStatus.CONSOLIDATED)

        graph_result = GraphProjectionRunner(self.connection, self.settings).run_once()
        self.messages.mark_processing_status(message_uuid, ProcessingStatus.PROJECTED)
        self.messages.mark_processing_status(message_uuid, ProcessingStatus.AVAILABLE)
        self.runs.mark_succeeded(
            run.processing_run_uuid,
            raw_output={
                "units": len(units),
                "jakobson_annotations": jakobson_result["annotations"],
                "memory_signal_routes": jakobson_result["routes"],
                "gate_decisions": len(decisions),
                "analyses": len(analyses),
                "candidates": len(candidates),
                "consolidation_decisions": len(decisions_created),
                "graph_projection": graph_result,
            },
        )

        return {
            "processing_run_uuid": run.processing_run_uuid,
            "message_uuid": message_uuid,
            "units": len(units),
            "jakobson_annotations": jakobson_result["annotations"],
            "memory_signal_routes": jakobson_result["routes"],
            "gate_decisions": len(decisions),
            "analyses": len(analyses),
            "candidates": len(candidates),
            "consolidation_decisions": len(decisions_created),
            "graph_projection": graph_result,
            "model_role": str(
                extraction_profile.get("model_role") or ModelRole.MEMORY_EXTRACTION.value
            ),
            "model_profile_uuid": extraction_profile_uuid,
        }

    def _completed_result(self, processing_run_uuid: str, message_uuid: str) -> dict[str, object]:
        unit_count = int(
            self.connection.execute(
                "SELECT COUNT(*) FROM text_units WHERE message_uuid = ?", (message_uuid,)
            ).fetchone()[0]
        )
        candidate_count = int(
            self.connection.execute(
                "SELECT COUNT(*) FROM memory_candidates WHERE processing_run_uuid = ?",
                (processing_run_uuid,),
            ).fetchone()[0]
        )
        return {
            "processing_run_uuid": processing_run_uuid,
            "message_uuid": message_uuid,
            "units": unit_count,
            "candidates": candidate_count,
            "idempotent_replay": True,
            "status": "succeeded",
        }

    def _resolve_memory_extraction_profile(
        self, override: dict[str, object] | None = None
    ) -> dict[str, object]:
        if override is not None:
            return override
        repository = ModelControlRepository(self.connection)
        resolved = repository.resolve_default(ModelRole.MEMORY_EXTRACTION)
        if resolved is None:
            return {
                "model_profile_uuid": None,
                "provider_type": "deterministic",
                "model_name": "deterministic_extraction",
            }
        return resolved

    def _unitize(self, message: Message, raw_text: str) -> list[TextUnit]:
        units = self.unitizer.to_text_units(
            message_uuid=message.message_uuid,
            session_uuid=message.session_uuid,
            speaker_role=message.role.value,
            text=raw_text,
        )
        stored_units = self.units.create_units(units)
        self.jobs.enqueue_job_once(
            JOB_MEMORY_GATING,
            {"message_uuid": message.message_uuid, "unit_count": len(stored_units)},
            priority=60,
        )
        return stored_units

    def _gate_units(
        self,
        processing_run_uuid: str,
        units: list[TextUnit],
    ) -> list[MemoryGateDecision]:
        decisions = [
            self.gates.create_decision(self.gate.to_model(unit, processing_run_uuid))
            for unit in units
        ]
        for decision in decisions:
            if decision.decision in {
                GateDecisionValue.ANALYZE,
                GateDecisionValue.ANALYZE_HIGH_CONFIDENCE,
                GateDecisionValue.MANUAL_REVIEW,
            }:
                job_type = (
                    JOB_HIGH_CONFIDENCE_EXTRACTION
                    if decision.requires_high_confidence_pass
                    else JOB_MEMORY_ANALYSIS
                )
                self.jobs.enqueue_job_once(
                    job_type,
                    {
                        "processing_run_uuid": processing_run_uuid,
                        "text_unit_uuid": decision.text_unit_uuid,
                    },
                    priority=70 if decision.requires_high_confidence_pass else 50,
                )
        return decisions

    def _analyze_units(
        self,
        message: Message,
        processing_run_uuid: str,
        units: list[TextUnit],
        decisions: list[MemoryGateDecision],
    ) -> list[LinguisticAnalysis]:
        eligible_unit_uuids = {
            decision.text_unit_uuid
            for decision in decisions
            if decision.decision
            in {
                GateDecisionValue.ANALYZE,
                GateDecisionValue.ANALYZE_HIGH_CONFIDENCE,
                GateDecisionValue.MANUAL_REVIEW,
            }
        }
        analyses: list[LinguisticAnalysis] = []
        for unit in units:
            if unit.text_unit_uuid not in eligible_unit_uuids:
                continue
            analysis = self.analyzer.analyze(
                unit,
                processing_run_uuid,
                source_timestamp=message.created_at,
                session_uuid=message.session_uuid,
            )
            analyses.append(self.analyses.create_analysis(analysis))
            self.jobs.enqueue_job_once(
                JOB_CANDIDATE_EXTRACTION,
                {
                    "processing_run_uuid": processing_run_uuid,
                    "text_unit_uuid": unit.text_unit_uuid,
                },
                priority=50,
            )
        return analyses

    def _extract_candidates(
        self,
        message: Message,
        processing_run_uuid: str,
        units: list[TextUnit],
    ) -> list[MemoryCandidate]:
        candidates: list[MemoryCandidate] = []
        for unit in units:
            analysis = self.analyses.get_for_unit(unit.text_unit_uuid, processing_run_uuid)
            if analysis is None:
                continue
            extracted = self.extractor.extract(message, unit, processing_run_uuid, analysis)
            for item in extracted:
                candidates.append(self.candidates.create_candidate(item.candidate, item.evidence))
                self.jobs.enqueue_job_once(
                    JOB_MEMORY_CONSOLIDATION,
                    {"candidate_uuid": item.candidate.candidate_uuid},
                    priority=50,
                )
        if candidates:
            self.jobs.enqueue_job_once(
                JOB_GRAPH_PROJECTION,
                {"message_uuid": message.message_uuid, "candidate_count": len(candidates)},
                priority=30,
            )
        return candidates


def _optional_string(value: object) -> str | None:
    return None if value is None else str(value)
