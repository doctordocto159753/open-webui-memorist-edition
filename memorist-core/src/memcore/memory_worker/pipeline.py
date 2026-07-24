import sqlite3
from collections.abc import Callable, Sequence
from hashlib import sha256
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
from memcore.memory_worker.jakobson.validator import validate_jakobson_provider_output
from memcore.memory_worker.prepared import PreparedJakobsonInference
from memcore.memory_worker.segmentation.sentence_segmenter import SentenceSegmenter
from memcore.memory_worker.semantic.authority import LiteCandidateAuthorityResolver
from memcore.memory_worker.semantic.project_artifact import structured_project_artifact
from memcore.model_control.repository import ModelControlRepository
from memcore.model_control.resolution import RoleResolutionService
from memcore.model_control.schemas import UsageEventCreate
from memcore.model_control.stage_contracts import (
    deterministic_high_confidence,
    deterministic_privacy,
    validate_high_confidence_result,
    validate_privacy_result,
)
from memcore.model_control.stage_invocation import StageInvocationRequest, StageInvoker
from memcore.models import (
    CandidateEvidence,
    CandidateStatus,
    CandidateType,
    EvidenceRole,
    Explicitness,
    GateDecisionValue,
    LinguisticAnalysis,
    MemoryCandidate,
    MemoryConsolidationDecision,
    MemoryGateDecision,
    MemoryVersionEmbedding,
    Message,
    ModelRole,
    ProcessingRunStatus,
    ProcessingStatus,
    SensitivityClass,
    SourceAuthority,
    SupportType,
    TextUnit,
    new_uuid,
    utc_now,
)
from memcore.repositories import JobRepository, MessageRepository, RepositoryError
from memcore.repositories.memory_worker import (
    GateDecisionRepository,
    LinguisticAnalysisRepository,
    MemoryCandidateRepository,
    MemoryProcessingRunRepository,
    TextUnitRepository,
)
from memcore.repositories.retrieval import RetrievalRepository
from memcore.validators.ijson import dump_ijson, load_ijson


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
        self.candidate_authority = LiteCandidateAuthorityResolver(connection)
        self.consolidator = MemoryConsolidator(connection)
        self.jakobson = JakobsonAnalysisService(connection, segmenter=self.unitizer)

    def prepare_message(
        self,
        message_uuid: str,
        model_target: dict[str, object] | None = None,
    ) -> PreparedJakobsonInference:
        """Run and validate provider inference without writing canonical state."""
        message = self.messages.get_message(message_uuid)
        if message is None:
            raise RepositoryError(f"Message not found: {message_uuid}")
        raw_text = message.raw_text or ""
        extraction_profile = self._resolve_memory_extraction_profile(message, model_target)
        provider_type = str(extraction_profile["provider_type"])
        model_name = str(extraction_profile.get("model_name") or provider_type)
        model_role = str(extraction_profile.get("model_role") or ModelRole.MEMORY_EXTRACTION.value)
        profile_uuid = _optional_string(extraction_profile.get("model_profile_uuid"))
        identity = build_processing_identity(
            target_message_uuid=message.message_uuid,
            raw_text=raw_text,
            model_target=extraction_profile,
            model_role=model_role,
        )
        units = self.unitizer.to_text_units(
            message_uuid=message.message_uuid,
            session_uuid=message.session_uuid,
            speaker_role=message.role.value,
            text=raw_text,
        )
        provider = (
            OpenAICompatibleJakobsonProvider(extraction_profile)
            if provider_type in {"openai_compatible", "openai_compatible_llm"}
            else DeterministicJakobsonProvider(
                provider_type=provider_type,
                model_name=model_name,
            )
        )
        started = perf_counter()
        output = provider.analyze(units, raw_text)
        validate_jakobson_provider_output(output)
        return PreparedJakobsonInference(
            message_uuid=message.message_uuid,
            model_role=model_role,
            model_profile_uuid=profile_uuid,
            provider_type=provider_type,
            model_name=model_name,
            processing_identity_hash=identity.identity_hash,
            input_content_hash=identity.input_content_hash,
            output=output,
            input_tokens=int(
                getattr(provider, "input_tokens", 0) or max(0, (len(raw_text) + 3) // 4)
            ),
            output_tokens=int(
                getattr(provider, "output_tokens", 0) or len(output.get("sentences", []))
            ),
            latency_ms=int(getattr(provider, "latency_ms", 0) or (perf_counter() - started) * 1000),
        )

    def process_message(
        self,
        message_uuid: str,
        import_run_uuid: str | None = None,
        job_uuid: str | None = None,
        model_target: dict[str, object] | None = None,
        lease_fence: Callable[[], None] | None = None,
        prepared_inference: PreparedJakobsonInference | None = None,
    ) -> dict[str, object]:
        message = self.messages.get_message(message_uuid)
        if message is None:
            raise RepositoryError(f"Message not found: {message_uuid}")
        raw_text = message.raw_text or ""
        extraction_profile = self._resolve_memory_extraction_profile(message, model_target)
        identity = build_processing_identity(
            target_message_uuid=message.message_uuid,
            raw_text=raw_text,
            model_target=extraction_profile,
            model_role=str(
                extraction_profile.get("model_role") or ModelRole.MEMORY_EXTRACTION.value
            ),
        )
        if prepared_inference is not None and (
            prepared_inference.processing_identity_hash != identity.identity_hash
            or prepared_inference.input_content_hash != identity.input_content_hash
        ):
            raise RepositoryError("prepared inference processing identity mismatch")
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
        if prepared_inference is not None:
            self.jakobson.provider = DeterministicJakobsonProvider(
                provider_type=provider_type,
                model_name=str(extraction_profile["model_name"]),
            )
        elif provider_type in {
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
            lease_fence=lease_fence,
            prepared_inference=prepared_inference,
        )
        ModelControlRepository(self.connection).record_usage_event(
            UsageEventCreate(
                role=ModelRole(
                    str(extraction_profile.get("model_role") or ModelRole.MEMORY_EXTRACTION.value)
                ),
                stage="jakobson_sentence_analysis",
                model_profile_uuid=extraction_profile_uuid,
                provider_type=provider_type,
                model_name=str(extraction_profile["model_name"]),
                session_uuid=message.session_uuid,
                message_uuid=message.message_uuid,
                import_run_uuid=import_run_uuid,
                job_uuid=job_uuid,
                input_tokens=int(jakobson_result.get("input_tokens", 0)),
                output_tokens=int(jakobson_result.get("output_tokens", 0)),
                latency_ms=int(
                    jakobson_result.get("latency_ms", 0)
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

        candidates = self._extract_candidates(
            message,
            run.processing_run_uuid,
            units,
            analysis_run_uuid=str(jakobson_result["analysis_run_uuid"]),
            import_run_uuid=import_run_uuid,
            provider_type=provider_type,
            model_name=str(extraction_profile["model_name"]),
        )
        stage_results = self._run_candidate_stages(
            message,
            run.processing_run_uuid,
            candidates,
        )
        self.messages.mark_processing_status(message_uuid, ProcessingStatus.CANDIDATES_CREATED)

        decisions_created = [
            self.consolidator.consolidate_candidate(candidate.candidate_uuid)
            for candidate in candidates
        ]
        embedding_results = self._embed_consolidated_versions(
            message,
            run.processing_run_uuid,
            decisions_created,
        )
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
                "candidate_stage_results": stage_results,
                "embedding_results": embedding_results,
                "graph_projection": graph_result,
            },
        )

        return {
            "processing_run_uuid": run.processing_run_uuid,
            "prompt_execution_uuid": jakobson_result.get("prompt_execution_uuid"),
            "message_uuid": message_uuid,
            "units": len(units),
            "jakobson_annotations": jakobson_result["annotations"],
            "memory_signal_routes": jakobson_result["routes"],
            "gate_decisions": len(decisions),
            "analyses": len(analyses),
            "candidates": len(candidates),
            "consolidation_decisions": len(decisions_created),
            "candidate_stage_results": stage_results,
            "embedding_results": embedding_results,
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
        self,
        message: Message,
        override: dict[str, object] | None = None,
    ) -> dict[str, object]:
        if override is not None:
            return override
        repository = ModelControlRepository(self.connection)
        scope = self.connection.execute(
            "SELECT workspace_uuid, project_uuid FROM sessions WHERE session_uuid = ?",
            (message.session_uuid,),
        ).fetchone()
        resolution = RoleResolutionService(repository).resolve(
            ModelRole.MEMORY_EXTRACTION,
            workspace_uuid=(
                str(scope["workspace_uuid"]) if scope and scope["workspace_uuid"] else None
            ),
            project_uuid=(
                str(scope["project_uuid"]) if scope and scope["project_uuid"] else None
            ),
        )
        if resolution.model_profile_uuid:
            profile = repository.get_profile(resolution.model_profile_uuid)
            if profile is not None:
                values: dict[str, object] = profile.model_dump(mode="json")
                values["model_role"] = ModelRole.MEMORY_EXTRACTION.value
                return values
        return resolution.runtime_profile()

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
        *,
        analysis_run_uuid: str,
        import_run_uuid: str | None,
        provider_type: str,
        model_name: str,
    ) -> list[MemoryCandidate]:
        candidates: list[MemoryCandidate] = []
        for unit in units:
            analysis = self.analyses.get_for_unit(unit.text_unit_uuid, processing_run_uuid)
            authority = self.candidate_authority.resolve(
                text_unit_uuid=unit.text_unit_uuid,
                processing_run_uuid=processing_run_uuid,
                analysis_run_uuid=analysis_run_uuid,
            )
            extracted = self.extractor.extract(
                message,
                unit,
                processing_run_uuid,
                analysis,
                authority=authority,
                imported_record=import_run_uuid is not None,
                provider_type=provider_type,
                model_name=model_name,
            )
            for item in extracted:
                candidates.append(self.candidates.create_candidate(item.candidate, item.evidence))
                self.jobs.enqueue_job_once(
                    JOB_MEMORY_CONSOLIDATION,
                    {"candidate_uuid": item.candidate.candidate_uuid},
                    priority=50,
                )
        artifact_candidate = self._structured_artifact_candidate(
            message,
            processing_run_uuid,
            units,
            analysis_run_uuid,
        )
        if artifact_candidate is not None:
            candidates.append(artifact_candidate)
            self.jobs.enqueue_job_once(
                JOB_MEMORY_CONSOLIDATION,
                {"candidate_uuid": artifact_candidate.candidate_uuid},
                priority=50,
            )
        if candidates:
            self.jobs.enqueue_job_once(
                JOB_GRAPH_PROJECTION,
                {"message_uuid": message.message_uuid, "candidate_count": len(candidates)},
                priority=30,
            )
        return candidates

    def _structured_artifact_candidate(
        self,
        message: Message,
        processing_run_uuid: str,
        units: list[TextUnit],
        analysis_run_uuid: str,
    ) -> MemoryCandidate | None:
        if not units or not message.raw_text:
            return None
        session = self.connection.execute(
            "SELECT workspace_uuid, project_uuid FROM sessions WHERE session_uuid = ?",
            (message.session_uuid,),
        ).fetchone()
        preceding = self.connection.execute(
            """
            SELECT message_uuid
            FROM messages
            WHERE session_uuid = ? AND role = 'user' AND created_at <= ?
              AND message_uuid <> ?
            ORDER BY created_at DESC, message_uuid DESC
            LIMIT 1
            """,
            (message.session_uuid, message.created_at, message.message_uuid),
        ).fetchone()
        artifact = structured_project_artifact(
            raw_text=message.raw_text,
            message_role=message.role,
            list_item_count=sum(unit.unit_type.value == "list_item" for unit in units),
            has_cross_session_scope=bool(
                session and (session["project_uuid"] or session["workspace_uuid"])
            ),
            preceding_user_message_uuid=(
                str(preceding["message_uuid"]) if preceding is not None else None
            ),
        )
        if artifact is None:
            return None
        execution = self.connection.execute(
            "SELECT prompt_execution_uuid FROM jakobson_analysis_runs "
            "WHERE analysis_run_uuid = ?",
            (analysis_run_uuid,),
        ).fetchone()
        candidate = MemoryCandidate(
            processing_run_uuid=processing_run_uuid,
            text_unit_uuid=units[0].text_unit_uuid,
            prompt_execution_uuid=(
                str(execution["prompt_execution_uuid"])
                if execution is not None and execution["prompt_execution_uuid"]
                else None
            ),
            candidate_type=CandidateType.EPISODE,
            subject_key=f"project_artifact:{message.message_uuid}",
            predicate="structured_project_artifact",
            object_ijson=dump_ijson(artifact.object_payload),
            normalized_text=artifact.normalized_text,
            source_authority=SourceAuthority(artifact.source_authority),
            explicitness=Explicitness(artifact.explicitness),
            confidence=artifact.confidence,
            importance=artifact.importance,
            status=CandidateStatus.READY_FOR_CONSOLIDATION,
            sensitivity_class=SensitivityClass.NORMAL,
            extraction_metadata_ijson=dump_ijson(artifact.metadata),
        )
        evidence = CandidateEvidence(
            candidate_uuid=candidate.candidate_uuid,
            message_uuid=message.message_uuid,
            text_unit_uuid=units[0].text_unit_uuid,
            evidence_text=message.raw_text,
            start_char=0,
            end_char=len(message.raw_text),
            evidence_role=EvidenceRole.PRIMARY,
            support_type=SupportType.SUPPORTING,
        )
        return self.candidates.create_candidate(candidate, [evidence])

    def _run_candidate_stages(
        self,
        message: Message,
        processing_run_uuid: str,
        candidates: list[MemoryCandidate],
    ) -> list[dict[str, object]]:
        repository = ModelControlRepository(self.connection)
        invoker = StageInvoker(self.connection, repository)
        scope = self.connection.execute(
            "SELECT workspace_uuid, project_uuid FROM sessions WHERE session_uuid = ?",
            (message.session_uuid,),
        ).fetchone()
        workspace_uuid = str(scope["workspace_uuid"]) if scope and scope["workspace_uuid"] else None
        project_uuid = str(scope["project_uuid"]) if scope and scope["project_uuid"] else None
        results: list[dict[str, object]] = []
        for candidate in candidates:
            evidence_rows = self.candidates.list_evidence(candidate.candidate_uuid)
            evidence_text = (
                evidence_rows[0].evidence_text
                if evidence_rows
                else candidate.normalized_text
            )
            privacy = invoker.invoke_structured(
                StageInvocationRequest(
                    role=ModelRole.PRIVACY_SENSITIVITY,
                    stage="privacy_sensitivity",
                    source_type="memory_candidate",
                    source_uuid=candidate.candidate_uuid,
                    processing_run_uuid=processing_run_uuid,
                    workspace_uuid=workspace_uuid,
                    project_uuid=project_uuid,
                    session_uuid=message.session_uuid,
                    message_uuid=message.message_uuid,
                    prompt_id="memorist.privacy_sensitivity",
                    prompt_version="2.0",
                    input_payload={
                        "candidate_text": candidate.normalized_text,
                        "evidence_text": evidence_text,
                        "source_authority": candidate.source_authority.value,
                    },
                ),
                validator=validate_privacy_result,
                deterministic_output=deterministic_privacy,
            )
            privacy_classification = str(
                (privacy.output or {}).get("classification") or "abstain"
            )
            self._apply_privacy_result(candidate, privacy_classification)
            results.append(privacy.model_dump(mode="json", exclude={"output"}))

            metadata = (
                load_ijson(candidate.extraction_metadata_ijson)
                if candidate.extraction_metadata_ijson
                else {}
            )
            if not bool(metadata.get("requires_high_confidence_pass")):
                continue
            high_confidence = invoker.invoke_structured(
                StageInvocationRequest(
                    role=ModelRole.HIGH_CONFIDENCE_EXTRACTION,
                    stage="high_confidence_extraction",
                    source_type="memory_candidate",
                    source_uuid=candidate.candidate_uuid,
                    processing_run_uuid=processing_run_uuid,
                    workspace_uuid=workspace_uuid,
                    project_uuid=project_uuid,
                    session_uuid=message.session_uuid,
                    message_uuid=message.message_uuid,
                    prompt_id="memorist.high_confidence_extraction",
                    prompt_version="1.0",
                    input_payload={
                        "candidate_text": candidate.normalized_text,
                        "evidence_text": evidence_text,
                        "source_authority": candidate.source_authority.value,
                        "route_type": metadata.get("route_type"),
                    },
                ),
                validator=validate_high_confidence_result,
                deterministic_output=deterministic_high_confidence,
            )
            decision = str((high_confidence.output or {}).get("decision") or "abstain")
            self._apply_high_confidence_result(candidate, decision, high_confidence.execution_uuid)
            results.append(high_confidence.model_dump(mode="json", exclude={"output"}))
        return results

    def _apply_privacy_result(
        self,
        candidate: MemoryCandidate,
        classification: str,
    ) -> None:
        status = candidate.status.value
        sensitivity = candidate.sensitivity_class.value
        if classification == "secret":
            status, sensitivity = "rejected", "secret"
        elif classification in {"sensitive", "requires_review", "abstain"}:
            if status != "rejected":
                status = "needs_review"
            sensitivity = "sensitive" if classification == "sensitive" else sensitivity
        with self.connection:
            self.connection.execute(
                "UPDATE memory_candidates SET status = ?, sensitivity_class = ? "
                "WHERE candidate_uuid = ?",
                (status, sensitivity, candidate.candidate_uuid),
            )

    def _apply_high_confidence_result(
        self,
        candidate: MemoryCandidate,
        decision: str,
        execution_uuid: str,
    ) -> None:
        current = self.candidates.get_candidate(candidate.candidate_uuid)
        if current is None:
            return
        status = current.status.value
        if decision == "approved" and status not in {"rejected", "needs_review"}:
            status = "ready_for_consolidation"
        elif decision == "rejected":
            status = "rejected"
        elif decision in {"needs_review", "abstain"} and status != "rejected":
            status = "needs_review"
        metadata = (
            load_ijson(current.extraction_metadata_ijson)
            if current.extraction_metadata_ijson
            else {}
        )
        metadata["high_confidence_stage_status"] = decision
        metadata["high_confidence_execution_uuid"] = execution_uuid
        with self.connection:
            self.connection.execute(
                "UPDATE memory_candidates SET status = ?, extraction_metadata_ijson = ? "
                "WHERE candidate_uuid = ?",
                (status, dump_ijson(metadata), candidate.candidate_uuid),
            )

    def _embed_consolidated_versions(
        self,
        message: Message,
        processing_run_uuid: str,
        decisions: Sequence[MemoryConsolidationDecision],
    ) -> list[dict[str, object]]:
        repository = ModelControlRepository(self.connection)
        scope = self.connection.execute(
            "SELECT workspace_uuid, project_uuid FROM sessions WHERE session_uuid = ?",
            (message.session_uuid,),
        ).fetchone()
        workspace_uuid = str(scope["workspace_uuid"]) if scope and scope["workspace_uuid"] else None
        project_uuid = str(scope["project_uuid"]) if scope and scope["project_uuid"] else None
        invoker = StageInvoker(self.connection, repository)
        results: list[dict[str, object]] = []
        for decision in decisions:
            version_uuid = getattr(decision, "target_memory_version_uuid", None)
            memory_uuid = getattr(decision, "target_memory_uuid", None)
            if not version_uuid:
                continue
            row = self.connection.execute(
                "SELECT normalized_text FROM memory_versions WHERE memory_version_uuid = ?",
                (version_uuid,),
            ).fetchone()
            if row is None:
                continue
            text = str(row["normalized_text"])
            now = utc_now()
            payload = dump_ijson(
                {
                    "memory_uuid": memory_uuid,
                    "memory_version_uuid": version_uuid,
                    "content_hash": sha256(text.encode("utf-8")).hexdigest(),
                }
            )
            with self.connection:
                self.connection.execute(
                    """
                    INSERT OR IGNORE INTO embedding_outbox (
                        outbox_uuid, event_type, source_type, source_uuid, payload_ijson,
                        status, priority, attempts, run_after, created_at, updated_at,
                        schema_version
                    ) VALUES (?, 'memory_version_upserted', 'memory_version', ?, ?,
                              'pending', 25, 0, ?, ?, ?, 1)
                    """,
                    (
                        new_uuid(),
                        version_uuid,
                        payload,
                        now,
                        now,
                        now,
                    ),
                )
            resolution = invoker.resolver.resolve(
                ModelRole.EMBEDDING,
                workspace_uuid=workspace_uuid,
                project_uuid=project_uuid,
            )
            if resolution.provider_type == "disabled":
                continue
            profile = (
                repository.get_profile(resolution.model_profile_uuid)
                if resolution.model_profile_uuid
                else None
            )
            expected_dimension = profile.embedding_dimension if profile else None
            result, vectors = invoker.invoke_embedding(
                StageInvocationRequest(
                    role=ModelRole.EMBEDDING,
                    stage="embedding_generation",
                    source_type="memory_version",
                    source_uuid=str(version_uuid),
                    processing_run_uuid=processing_run_uuid,
                    workspace_uuid=workspace_uuid,
                    project_uuid=project_uuid,
                    session_uuid=message.session_uuid,
                    message_uuid=message.message_uuid,
                    prompt_version="1.0",
                    input_payload={"text": text, "content_hash": sha256(text.encode()).hexdigest()},
                ),
                texts=[text],
                expected_dimension=expected_dimension,
            )
            if vectors and result.model_profile_uuid:
                vector = vectors[0]
                content_hash = sha256(text.encode("utf-8")).hexdigest()
                RetrievalRepository(self.connection).upsert_embedding(
                    MemoryVersionEmbedding(
                        memory_version_uuid=str(version_uuid),
                        embedding_model=result.model_name,
                        embedding_dimension=len(vector),
                        embedding_version="1",
                        content_hash=content_hash,
                        embedding_ijson=dump_ijson(vector),
                    )
                )
                existing = self.connection.execute(
                    "SELECT 1 FROM embedding_records WHERE model_profile_uuid = ? "
                    "AND source_type = 'memory_version' AND source_uuid = ? "
                    "AND content_hash = ?",
                    (result.model_profile_uuid, version_uuid, content_hash),
                ).fetchone()
                if existing is None:
                    repository.record_embedding(
                        result.model_profile_uuid,
                        "memory_version",
                        str(version_uuid),
                        content_hash,
                        f"sqlite:memory_version_embeddings:{version_uuid}",
                        len(vector),
                    )
                with self.connection:
                    self.connection.execute(
                        "UPDATE embedding_outbox SET status = 'succeeded', updated_at = ? "
                        "WHERE event_type = 'memory_version_upserted' AND source_uuid = ?",
                        (now, version_uuid),
                    )
            results.append(result.model_dump(mode="json", exclude={"output"}))
        return results


def _optional_string(value: object) -> str | None:
    return None if value is None else str(value)
