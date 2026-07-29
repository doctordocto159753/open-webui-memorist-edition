"""The single shared WP02 semantic-candidate orchestration service.

Lite and Full provide storage adapters to this module.  All ordering, model
invocation, binding validation, coverage planning and proposal adaptation lives
here so the two runtimes cannot make different semantic decisions.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol, cast

from memcore.memory_worker.attempt_audit import (
    FrozenProviderExecution,
    ProviderAttemptAuditRepository,
    stable_stage_execution_uuid,
)
from memcore.memory_worker.execution import ContractExecutionOutcome
from memcore.memory_worker.extraction.sensitivity import classify_sensitivity
from memcore.memory_worker.identity import execution_profile_fingerprint
from memcore.memory_worker.prompts.contracts import (
    SEMANTIC_CANDIDATE_V1_CONTRACT,
    SemanticAnalysisV1Output,
)
from memcore.memory_worker.prompts.versions import (
    SEMANTIC_CANDIDATE_ANALYSIS_PROMPT_ID,
    SEMANTIC_CANDIDATE_ANALYSIS_VERSION,
)
from memcore.memory_worker.semantic.bounded_context import (
    BoundedContextResolver,
    BoundedContextSource,
)
from memcore.memory_worker.semantic.candidate_mapping import (
    ROUTE_CANDIDATE_MAPPING_VERSION,
)
from memcore.memory_worker.semantic.candidate_service import (
    build_candidate_from_proposal,
)
from memcore.memory_worker.semantic.coverage import (
    CandidateProposal,
    CoveragePlan,
    CoveragePlannerInput,
    PersistedUnitAuthority,
    plan_candidate_coverage,
)
from memcore.memory_worker.semantic.coverage.identity import semantic_unit_fingerprint
from memcore.memory_worker.semantic.provenance_policy import PROVENANCE_POLICY_VERSION
from memcore.memory_worker.semantic_contract import (
    SemanticAnalysisV1Input,
    build_semantic_input,
    validate_semantic_binding,
)
from memcore.memory_worker.semantic_coverage_persistence import (
    CandidateAuthorityBinding,
    CoveragePersistenceBindings,
    candidate_payload_hash,
)
from memcore.memory_worker.semantic_runtime import (
    execute_semantic_candidate_contract,
    semantic_abstention,
)
from memcore.models import CandidateEvidence, MemoryCandidate, ModelRole, SensitivityClass
from memcore.textsemantics import TEXT_SEMANTICS_CONTRACT_VERSION, build_envelope
from memcore.validators.ijson import canonical_hash_ijson

SEMANTIC_ORCHESTRATION_VERSION = "memorist.semantic_candidate.orchestration.v1"
SEMANTIC_PRIVACY_POLICY_VERSION = "wp02-privacy-ceiling-v1"
_TERMINAL_GATES = {"discard", "retain_raw_only"}


@dataclass(frozen=True)
class RecordedSemanticExecution:
    prompt_execution_uuid: str
    stage_execution_uuid: str
    output: dict[str, Any]


@dataclass(frozen=True)
class RecordedSemanticPlanningReplay:
    plan: CoveragePlan
    candidate_uuids: tuple[str, ...]
    semantic_stage_execution_uuid: str | None


class SemanticCandidateRuntimeAdapter(BoundedContextSource, Protocol):
    """Storage and audit mechanics required by the shared service."""

    connection: Any
    postgres: bool

    def load_persisted_authorities(
        self,
        *,
        message_uuid: str,
        processing_run_uuid: str,
    ) -> Sequence[PersistedUnitAuthority]: ...

    def load_completed_semantic_planning(
        self,
        *,
        message_uuid: str,
        processing_run_uuid: str,
    ) -> RecordedSemanticPlanningReplay | None: ...

    def load_semantic_execution(
        self,
        *,
        stage_execution_uuid: str,
        input_hash: str,
        contract_hash: str,
    ) -> RecordedSemanticExecution | None: ...

    def record_semantic_execution(
        self,
        *,
        prompt_execution_uuid: str,
        stage_execution_uuid: str,
        processing_run_uuid: str,
        input_payload: Mapping[str, Any],
        outcome: ContractExecutionOutcome,
        profile: Mapping[str, Any],
        message_uuid: str,
        import_run_uuid: str | None,
        job_uuid: str | None,
        contract_hash: str,
        profile_fingerprint: str,
    ) -> None: ...

    def assert_runtime_snapshot(
        self,
        *,
        message_uuid: str,
        processing_run_uuid: str,
        raw_text_hash: str,
    ) -> None: ...

    def persist_coverage_plan(
        self,
        plan: CoveragePlan,
        bindings: CoveragePersistenceBindings,
    ) -> dict[str, Any]: ...

    def reserve_and_link_candidate(
        self,
        *,
        proposal: CandidateProposal,
        coverage_item_id: str,
        candidate: MemoryCandidate,
        evidence: CandidateEvidence,
        payload_hash: str,
        authority: CandidateAuthorityBinding,
    ) -> dict[str, Any]: ...


@dataclass(frozen=True)
class SemanticCandidatePlanningRequest:
    message_uuid: str
    processing_run_uuid: str
    profile: dict[str, Any]
    import_run_uuid: str | None = None
    job_uuid: str | None = None
    lease_fence: Callable[[], None] | None = None


@dataclass(frozen=True)
class SemanticCandidatePlanningResult:
    plan: CoveragePlan
    proposals: tuple[CandidateProposal, ...]
    candidates: tuple[MemoryCandidate, ...]
    semantic_prompt_execution_uuid: str | None
    semantic_stage_execution_uuid: str | None
    semantic_status: str
    semantic_called_provider: bool
    semantic_fallback_used: bool
    context_item_count: int
    terminal_gate_short_circuit: bool
    candidate_uuids: tuple[str, ...]

    @property
    def proposal_count(self) -> int:
        return sum(item.proposal_id is not None for item in self.plan.items)


class SemanticCandidatePlanningService:
    """Execute the frozen gate-before-semantic-before-candidate sequence."""

    def __init__(
        self,
        adapter: SemanticCandidateRuntimeAdapter,
        *,
        context_resolver: BoundedContextResolver | None = None,
    ) -> None:
        self.adapter = adapter
        self.context_resolver = context_resolver or BoundedContextResolver()

    def execute(
        self,
        request: SemanticCandidatePlanningRequest,
    ) -> SemanticCandidatePlanningResult:
        replay = self.adapter.load_completed_semantic_planning(
            message_uuid=request.message_uuid,
            processing_run_uuid=request.processing_run_uuid,
        )
        if replay is not None:
            return SemanticCandidatePlanningResult(
                plan=replay.plan,
                proposals=(),
                candidates=(),
                semantic_prompt_execution_uuid=replay.plan.semantic_prompt_execution_uuid,
                semantic_stage_execution_uuid=replay.semantic_stage_execution_uuid,
                semantic_status="replayed",
                semantic_called_provider=False,
                semantic_fallback_used=False,
                context_item_count=0,
                terminal_gate_short_circuit=replay.plan.status == "retain_raw_only",
                candidate_uuids=replay.candidate_uuids,
            )

        scope = self.adapter.load_current_context_scope(request.message_uuid)
        envelope = build_envelope(scope.raw_text)
        context = self.context_resolver.resolve(
            self.adapter,
            message_uuid=request.message_uuid,
            text_envelope=envelope,
        )
        semantic_input = build_semantic_input(
            current_message_uuid=scope.message_uuid,
            current_message_version_uuid=scope.message_version_uuid,
            current_raw_text=scope.raw_text,
            text_envelope=envelope,
            bounded_context_items=list(context.items),
            boundary=context.boundary,
        )
        authorities = tuple(
            self.adapter.load_persisted_authorities(
                message_uuid=request.message_uuid,
                processing_run_uuid=request.processing_run_uuid,
            )
        )
        if not context.authority_complete:
            authorities = tuple(
                authority.model_copy(update={"conflicting_authority": True})
                for authority in authorities
            )

        terminal_short_circuit = any(
            authority.gate_decision in _TERMINAL_GATES for authority in authorities
        )
        privacy_short_circuit = (
            any(
                not authority.privacy_storage_allowed
                or authority.privacy_ceiling in {"sensitive", "secret"}
                for authority in authorities
            )
            or classify_sensitivity(scope.raw_text) is not SensitivityClass.NORMAL
        )
        prompt_execution_uuid: str | None = None
        stage_execution_uuid: str | None = None
        called_provider = False
        fallback_used = False
        if terminal_short_circuit:
            semantic_output = SemanticAnalysisV1Output.model_validate(
                semantic_abstention("terminal_gate_before_semantic_analysis")
            )
            semantic_status = "skipped_by_gate"
        elif privacy_short_circuit:
            # Do not duplicate sensitive current-message content in a remote
            # semantic call or its replay/audit output. The canonical message
            # remains the sole content-bearing record and coverage fails closed.
            semantic_output = SemanticAnalysisV1Output.model_validate(
                semantic_abstention("privacy_ceiling_before_semantic_analysis")
            )
            semantic_status = "skipped_by_privacy"
        else:
            (
                semantic_output,
                prompt_execution_uuid,
                stage_execution_uuid,
                semantic_status,
                called_provider,
                fallback_used,
            ) = self._execute_or_replay_semantic(
                request,
                semantic_input,
                raw_text_hash=envelope.raw_text_hash,
            )

        report = validate_semantic_binding(
            semantic_input.model_dump(mode="json"),
            semantic_output.model_dump(mode="json"),
        )
        planner_input = CoveragePlannerInput(
            message_uuid=scope.message_uuid,
            message_version_uuid=scope.message_version_uuid,
            message_role=_planner_role(scope.role),
            processing_run_uuid=request.processing_run_uuid,
            current_raw_text=scope.raw_text,
            text_envelope=envelope.as_dict(),
            semantic_analysis=semantic_output,
            accepted_unit_ids=report.accepted_unit_ids,
            accepted_reference_indexes=report.accepted_reference_indexes,
            accepted_relation_indexes=report.accepted_relation_indexes,
            authorities=authorities,
            semantic_prompt_execution_uuid=prompt_execution_uuid,
            semantic_contract_hash=SEMANTIC_CANDIDATE_V1_CONTRACT.contract_hash,
            bounded_context_items=context.items,
            imported_record=request.import_run_uuid is not None,
            route_mapping_version=ROUTE_CANDIDATE_MAPPING_VERSION,
            provenance_policy_version=PROVENANCE_POLICY_VERSION,
            privacy_policy_version=SEMANTIC_PRIVACY_POLICY_VERSION,
        )
        plan, proposals = plan_candidate_coverage(planner_input)
        fingerprints = {
            unit.id: semantic_unit_fingerprint(
                unit=unit,
                analysis=semantic_output,
                accepted_reference_indexes=report.accepted_reference_indexes,
                accepted_relation_indexes=report.accepted_relation_indexes,
                context_items=context.items,
            )
            for unit in semantic_output.semantic_units
            if unit.id in report.accepted_unit_ids
        }
        annotation_uuids = {
            item.coverage_item_id: _annotation_for_item(item.raw_start, item.raw_end, authorities)
            for item in plan.items
        }
        self._fence_and_revalidate(
            request,
            raw_text_hash=envelope.raw_text_hash,
        )
        self.adapter.persist_coverage_plan(
            plan,
            CoveragePersistenceBindings(
                message_version_uuid=scope.message_version_uuid,
                text_envelope_contract_version=TEXT_SEMANTICS_CONTRACT_VERSION,
                semantic_unit_fingerprints=fingerprints,
                annotation_uuids=annotation_uuids,
            ),
        )

        candidates: list[MemoryCandidate] = []
        items_by_proposal = {
            item.proposal_id: item for item in plan.items if item.proposal_id is not None
        }
        authorities_by_unit = {item.text_unit_uuid: item for item in authorities}
        for proposal in proposals:
            coverage_item = items_by_proposal[proposal.proposal_id]
            authority = authorities_by_unit.get(proposal.text_unit_uuid)
            if authority is None:
                raise ValueError("proposal lost its persisted authority before adaptation")
            candidate, evidence = build_candidate_from_proposal(
                proposal,
                processing_run_uuid=request.processing_run_uuid,
            )
            payload_hash = candidate_payload_hash(candidate, (evidence,))
            self._fence_and_revalidate(
                request,
                raw_text_hash=envelope.raw_text_hash,
            )
            self.adapter.reserve_and_link_candidate(
                proposal=proposal,
                coverage_item_id=coverage_item.coverage_item_id,
                candidate=candidate,
                evidence=evidence,
                payload_hash=payload_hash,
                authority=_candidate_authority_binding(
                    request.processing_run_uuid,
                    proposal,
                    authority,
                ),
            )
            candidates.append(candidate)

        return SemanticCandidatePlanningResult(
            plan=plan,
            proposals=proposals,
            candidates=tuple(candidates),
            semantic_prompt_execution_uuid=prompt_execution_uuid,
            semantic_stage_execution_uuid=stage_execution_uuid,
            semantic_status=semantic_status,
            semantic_called_provider=called_provider,
            semantic_fallback_used=fallback_used,
            context_item_count=len(context.items),
            terminal_gate_short_circuit=terminal_short_circuit,
            candidate_uuids=tuple(candidate.candidate_uuid for candidate in candidates),
        )

    def _execute_or_replay_semantic(
        self,
        request: SemanticCandidatePlanningRequest,
        semantic_input: SemanticAnalysisV1Input,
        *,
        raw_text_hash: str,
    ) -> tuple[SemanticAnalysisV1Output, str, str, str, bool, bool]:
        input_payload = semantic_input.model_dump(mode="json")
        input_hash = canonical_hash_ijson(input_payload)
        contract_hash = SEMANTIC_CANDIDATE_V1_CONTRACT.contract_hash
        profile_fingerprint = execution_profile_fingerprint(request.profile)
        identity = canonical_hash_ijson(
            {
                "orchestration_version": SEMANTIC_ORCHESTRATION_VERSION,
                "processing_run_uuid": request.processing_run_uuid,
                "message_uuid": request.message_uuid,
                "prompt_id": SEMANTIC_CANDIDATE_ANALYSIS_PROMPT_ID,
                "prompt_version": SEMANTIC_CANDIDATE_ANALYSIS_VERSION,
                "contract_hash": contract_hash,
                "input_hash": input_hash,
                "profile_fingerprint": profile_fingerprint,
            }
        )
        stage_execution_uuid = stable_stage_execution_uuid(identity)
        replay = self.adapter.load_semantic_execution(
            stage_execution_uuid=stage_execution_uuid,
            input_hash=input_hash,
            contract_hash=contract_hash,
        )
        if replay is not None:
            output = SemanticAnalysisV1Output.model_validate(replay.output)
            validate_semantic_binding(input_payload, output.model_dump(mode="json"))
            return (
                output,
                replay.prompt_execution_uuid,
                replay.stage_execution_uuid,
                "replayed",
                False,
                False,
            )

        profile = request.profile
        provider_type = str(
            profile.get("provider_type") or profile.get("provider") or "deterministic"
        )
        model_name = str(profile.get("model_name") or provider_type)
        requested_role = str(
            profile.get("requested_role")
            or profile.get("model_role")
            or ModelRole.MEMORY_EXTRACTION.value
        )
        effective_role = str(
            profile.get("effective_role")
            or profile.get("model_role")
            or ModelRole.MEMORY_EXTRACTION.value
        )
        frozen = FrozenProviderExecution(
            stage_execution_uuid=stage_execution_uuid,
            processing_run_uuid=request.processing_run_uuid,
            job_uuid=request.job_uuid,
            source_type="message",
            source_uuid=request.message_uuid,
            requested_role=requested_role,
            effective_role=effective_role,
            model_profile_uuid=_optional_string(profile.get("model_profile_uuid")),
            profile_fingerprint=profile_fingerprint,
            scope_source=str(profile.get("scope_source") or "runtime_profile"),
            inheritance_source=_optional_string(profile.get("inheritance_source")),
            provider_type=provider_type,
            model_name=model_name,
            capability_mode=_capability_mode(profile),
            prompt_id=SEMANTIC_CANDIDATE_ANALYSIS_PROMPT_ID,
            prompt_version=SEMANTIC_CANDIDATE_ANALYSIS_VERSION,
            contract_hash=contract_hash,
            input_hash=input_hash,
            idempotency_identity=identity,
            deterministic_fallback_version="semantic-abstention-v1",
        )
        attempt_audit = ProviderAttemptAuditRepository(
            self.adapter.connection,
            frozen,
            postgres=self.adapter.postgres,
        )

        def revalidate() -> None:
            self._fence_and_revalidate(request, raw_text_hash=raw_text_hash)

        outcome = execute_semantic_candidate_contract(
            profile=profile,
            input_payload=semantic_input,
            revalidate=revalidate,
            attempt_audit=attempt_audit,
        )
        output = SemanticAnalysisV1Output.model_validate(outcome.output)
        validate_semantic_binding(input_payload, output.model_dump(mode="json"))
        prompt_execution_uuid = stable_stage_execution_uuid(
            f"semantic-prompt:{stage_execution_uuid}"
        )
        self._fence_and_revalidate(request, raw_text_hash=raw_text_hash)
        self.adapter.record_semantic_execution(
            prompt_execution_uuid=prompt_execution_uuid,
            stage_execution_uuid=stage_execution_uuid,
            processing_run_uuid=request.processing_run_uuid,
            input_payload=input_payload,
            outcome=outcome,
            profile=profile,
            message_uuid=request.message_uuid,
            import_run_uuid=request.import_run_uuid,
            job_uuid=request.job_uuid,
            contract_hash=contract_hash,
            profile_fingerprint=profile_fingerprint,
        )
        return (
            output,
            prompt_execution_uuid,
            stage_execution_uuid,
            outcome.status,
            outcome.called_provider,
            outcome.fallback_used,
        )

    def _fence_and_revalidate(
        self,
        request: SemanticCandidatePlanningRequest,
        *,
        raw_text_hash: str,
    ) -> None:
        if request.lease_fence is not None:
            request.lease_fence()
        self.adapter.assert_runtime_snapshot(
            message_uuid=request.message_uuid,
            processing_run_uuid=request.processing_run_uuid,
            raw_text_hash=raw_text_hash,
        )


def _candidate_authority_binding(
    processing_run_uuid: str,
    proposal: CandidateProposal,
    authority: PersistedUnitAuthority,
) -> CandidateAuthorityBinding:
    if any(
        value is None
        for value in (
            authority.gate_decision_uuid,
            authority.gate_decision,
            authority.annotation_uuid,
            authority.route_uuid,
            authority.route_type,
            authority.route_status,
        )
    ):
        raise ValueError("durable proposal requires complete persisted authority")
    return CandidateAuthorityBinding(
        processing_run_uuid=processing_run_uuid,
        text_unit_uuid=proposal.text_unit_uuid,
        gate_decision_uuid=str(authority.gate_decision_uuid),
        gate_decision=str(authority.gate_decision),
        annotation_uuid=str(authority.annotation_uuid),
        route_uuid=str(authority.route_uuid),
        route_type=str(authority.route_type),
        route_status=str(authority.route_status),
    )


def _annotation_for_item(
    raw_start: int,
    raw_end: int,
    authorities: Sequence[PersistedUnitAuthority],
) -> str | None:
    matching = [
        authority
        for authority in authorities
        if authority.raw_start <= raw_start and raw_end <= authority.raw_end
    ]
    return matching[0].annotation_uuid if len(matching) == 1 else None


def _planner_role(value: str) -> Literal["user", "assistant", "tool", "system"]:
    if value in {"user", "assistant", "tool", "system"}:
        return cast(Literal["user", "assistant", "tool", "system"], value)
    return "system"


def _capability_mode(profile: Mapping[str, Any]) -> str:
    if bool(profile.get("supports_structured_output")):
        return "json_schema"
    if bool(profile.get("supports_json_mode")):
        return "json"
    return "text"


def _optional_string(value: Any) -> str | None:
    return str(value) if value not in {None, ""} else None
