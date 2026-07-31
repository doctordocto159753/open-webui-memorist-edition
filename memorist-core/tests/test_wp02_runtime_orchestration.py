from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

from memcore.memory_worker.execution import ContractExecutionOutcome
from memcore.memory_worker.semantic.bounded_context import (
    BoundedContextResolver,
    CurrentContextScope,
    PriorContextRecord,
)
from memcore.memory_worker.semantic.coverage import PersistedUnitAuthority
from memcore.memory_worker.semantic.orchestration import (
    SemanticCandidatePlanningRequest,
    SemanticCandidatePlanningService,
)
from memcore.textsemantics import build_envelope

ROOT = Path(__file__).resolve().parents[1]


def test_exactly_one_shared_semantic_candidate_orchestration_service_exists() -> None:
    definitions: list[Path] = []
    for path in (ROOT / "src/memcore").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if any(
            isinstance(node, ast.ClassDef) and node.name == "SemanticCandidatePlanningService"
            for node in ast.walk(tree)
        ):
            definitions.append(path)
    assert definitions == [ROOT / "src/memcore/memory_worker/semantic/orchestration.py"]
    lite = (ROOT / "src/memcore/memory_worker/pipeline.py").read_text(encoding="utf-8")
    full = (ROOT / "src/memcore/memory_worker/postgres/pipeline.py").read_text(encoding="utf-8")
    assert "SemanticCandidatePlanningService(" in lite
    assert "SemanticCandidatePlanningService(" in full
    assert "_extract_candidates(" not in _process_message_source(lite)
    assert "_record_candidates(" not in _process_message_source(full)


def test_bounded_context_is_latest_two_ordered_units_and_is_deterministic() -> None:
    scope = _scope(raw_text="همان را نگه دار.")
    source = _ContextSource(
        scope,
        [
            _record(turn=1, unit=0, text="اول"),
            _record(turn=2, unit=0, text="دوم"),
            _record(turn=3, unit=0, text="سوم"),
        ],
    )
    resolver = BoundedContextResolver()

    first = resolver.resolve(
        source,
        message_uuid=scope.message_uuid,
        text_envelope=build_envelope("یک پیام مستقل."),
    )
    second = resolver.resolve(
        source,
        message_uuid=scope.message_uuid,
        text_envelope=build_envelope("یک پیام مستقل."),
    )

    assert [item.text for item in first.items] == ["دوم", "سوم"]
    assert [item.turn_index for item in first.items] == [2, 3]
    assert [item.context_item_id for item in first.items] == [
        item.context_item_id for item in second.items
    ]
    assert first.boundary.effective_limit == 2


def test_dependency_hint_expands_only_to_six_prior_units() -> None:
    scope = _scope(raw_text="همان را نگه دار.")
    source = _ContextSource(
        scope,
        [_record(turn=index, unit=0, text=f"متن {index}") for index in range(1, 9)],
    )
    result = BoundedContextResolver().resolve(
        source,
        message_uuid=scope.message_uuid,
        text_envelope=build_envelope(scope.raw_text),
    )

    assert result.boundary.effective_limit == 6
    assert result.boundary.dependency_expansion is True
    assert [item.turn_index for item in result.items] == [3, 4, 5, 6, 7, 8]


def test_context_fail_closed_excludes_cross_scope_hidden_roles_stale_and_secrets() -> None:
    scope = _scope(raw_text="این مورد.")
    records = [
        _record(turn=1, unit=0, text="eligible"),
        _record(turn=2, unit=0, text="cross", session_uuid="session-other"),
        _record(turn=3, unit=0, text="hidden", visibility="hidden"),
        _record(turn=4, unit=0, text="deleted", is_deleted=True),
        _record(turn=5, unit=0, text="redacted", redaction_status="redacted"),
        _record(turn=6, unit=0, text="system", role="system"),
        _record(turn=7, unit=0, text="tool", role="tool"),
        _record(
            turn=8,
            unit=0,
            text="stale",
            version_raw_text="different",
        ),
        _record(turn=9, unit=0, text="api_" + "key=" + "sk-" + "secret-value"),
    ]
    result = BoundedContextResolver().resolve(
        _ContextSource(scope, records),
        message_uuid=scope.message_uuid,
        text_envelope=build_envelope(scope.raw_text),
    )

    assert [item.text for item in result.items] == ["eligible"]
    assert {
        "cross_authority_boundary",
        "hidden_deleted_or_redacted",
        "role_excluded",
        "stale_or_invalid_text_unit_span",
        "sensitive_context_excluded",
    }.issubset(result.exclusion_reason_codes)


def test_terminal_gate_is_legacy_annotation_and_semantic_model_still_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _TerminalAdapter()
    _stub_semantic_provider(monkeypatch, units=[])
    result = SemanticCandidatePlanningService(adapter).execute(
        SemanticCandidatePlanningRequest(
            message_uuid=adapter.scope.message_uuid,
                processing_run_uuid="00000000-0000-4000-8000-000000000100",
            profile={
                "provider_type": "openai_compatible_llm",
                "model_name": "must-not-run",
                "model_role": "memory_extraction",
            },
        )
    )

    assert result.terminal_gate_short_circuit is False
    assert result.semantic_called_provider is True
    assert result.semantic_prompt_execution_uuid is not None
    assert result.proposals == ()
    assert result.candidates == ()
    assert {item.disposition.value for item in result.plan.items} == {"unsupported"}
    assert adapter.record_calls == 1
    assert adapter.persisted_plan is result.plan


def test_one_legacy_terminal_unit_does_not_veto_meaningful_sibling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _MixedTerminalAdapter()
    start = adapter.scope.raw_text.index("Keep")
    _stub_semantic_provider(
        monkeypatch,
        units=[
            {
                "id": "unit-keep",
                "raw_start": start,
                "raw_end": len(adapter.scope.raw_text),
                "evidence": adapter.scope.raw_text[start:],
                "proposition": "Backups must remain enabled.",
                "unit_type": "instruction",
                "durability": "durable",
                "polarity": "affirmed",
                "epistemic_status": "asserted",
            }
        ],
    )
    result = SemanticCandidatePlanningService(adapter).execute(
        SemanticCandidatePlanningRequest(
            message_uuid=adapter.scope.message_uuid,
                processing_run_uuid="00000000-0000-4000-8000-000000000100",
            profile={
                "provider_type": "openai_compatible_llm",
                "model_name": "must-not-run",
                "model_role": "memory_extraction",
            },
        )
    )

    assert result.terminal_gate_short_circuit is False
    assert result.semantic_called_provider is True
    assert result.semantic_prompt_execution_uuid is not None
    assert len(result.proposals) == 1
    assert len(result.candidates) == 1
    assert adapter.record_calls == 1
    assert adapter.persisted_plan is result.plan
    assert [
        (
            adapter.scope.raw_text[item.raw_start : item.raw_end],
            item.disposition.value,
            item.reason_codes,
        )
        for item in result.plan.items
    ] == [
        ("Discard", "unsupported", ("uncovered_material",)),
        ("Keep backups enabled.", "durable_candidate", ("durable_policy_eligible",)),
    ]


def test_sensitive_message_never_reaches_semantic_provider_or_prompt_audit() -> None:
    adapter = _PrivacyAdapter()
    result = SemanticCandidatePlanningService(adapter).execute(
        SemanticCandidatePlanningRequest(
            message_uuid=adapter.scope.message_uuid,
            processing_run_uuid="00000000-0000-4000-8000-000000000100",
            profile={
                "provider_type": "openai_compatible_llm",
                "model_name": "must-not-run",
                "model_role": "memory_extraction",
            },
        )
    )

    assert result.semantic_status == "skipped_by_privacy"
    assert result.semantic_called_provider is False
    assert result.semantic_prompt_execution_uuid is None
    assert result.proposals == ()
    assert result.candidates == ()
    assert result.plan.status == "abstain"
    assert adapter.record_calls == 0
    assert adapter.persisted_plan is result.plan


class _ContextSource:
    def __init__(
        self,
        scope: CurrentContextScope,
        records: list[PriorContextRecord],
    ) -> None:
        self.scope = scope
        self.records = records

    def load_current_context_scope(self, message_uuid: str) -> CurrentContextScope:
        assert message_uuid == self.scope.message_uuid
        return self.scope

    def list_prior_context_records(
        self,
        scope: CurrentContextScope,
        *,
        scan_limit: int,
    ) -> tuple[PriorContextRecord, ...]:
        assert scope == self.scope
        return tuple(self.records[:scan_limit])


class _TerminalAdapter(_ContextSource):
    connection = None
    postgres = False

    def __init__(self) -> None:
        self.scope = _scope(raw_text="این متن حذف شود.")
        self.records: list[PriorContextRecord] = []
        self.record_calls = 0
        self.persisted_plan: Any = None

    def load_persisted_authorities(
        self,
        *,
        message_uuid: str,
        processing_run_uuid: str,
    ) -> tuple[PersistedUnitAuthority, ...]:
        assert message_uuid == self.scope.message_uuid
        assert processing_run_uuid == "00000000-0000-4000-8000-000000000100"
        return (
            PersistedUnitAuthority(
                text_unit_uuid="00000000-0000-4000-8000-000000000101",
                raw_start=0,
                raw_end=len(self.scope.raw_text),
                annotation_uuid="annotation",
                gate_decision_uuid="gate",
                gate_decision="discard",
                route_uuid="route",
                route_type="ignore",
                route_status="ignored",
                privacy_ceiling="normal",
                privacy_storage_allowed=True,
            ),
        )

    def load_completed_semantic_planning(self, **_: Any) -> None:
        return None

    def load_semantic_execution(self, **_: Any) -> None:
        return None

    def record_semantic_execution(self, **_: Any) -> None:
        self.record_calls += 1

    def assert_runtime_snapshot(self, **_: Any) -> None:
        return None

    def persist_coverage_plan(self, plan: Any, bindings: Any) -> dict[str, Any]:
        del bindings
        self.persisted_plan = plan
        return {"state": "created"}

    def reserve_and_link_candidate(self, **_: Any) -> dict[str, Any]:
        return {"state": "candidate_linked"}


class _PrivacyAdapter(_TerminalAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.scope = _scope(raw_text="Bearer abcdefghijklmnop must stay private.")

    def load_persisted_authorities(
        self,
        *,
        message_uuid: str,
        processing_run_uuid: str,
    ) -> tuple[PersistedUnitAuthority, ...]:
        assert message_uuid == self.scope.message_uuid
        assert processing_run_uuid == "00000000-0000-4000-8000-000000000100"
        return (
            PersistedUnitAuthority(
                text_unit_uuid="00000000-0000-4000-8000-000000000101",
                raw_start=0,
                raw_end=len(self.scope.raw_text),
                annotation_uuid="annotation",
                gate_decision_uuid="gate",
                gate_decision="manual_review",
                route_uuid="route",
                route_type="task_constraint",
                route_status="ready",
                privacy_ceiling="normal",
                privacy_storage_allowed=True,
            ),
        )

    def load_semantic_execution(self, **_: Any) -> None:
        raise AssertionError("privacy ceiling must precede semantic replay/model execution")

    def record_semantic_execution(self, **_: Any) -> None:
        self.record_calls += 1
        raise AssertionError("privacy ceiling must not create semantic prompt audit")

    def reserve_and_link_candidate(self, **_: Any) -> dict[str, Any]:
        raise AssertionError("privacy ceiling must not reserve a candidate")


class _MixedTerminalAdapter(_TerminalAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.scope = _scope(raw_text="Discard. Keep backups enabled.")

    def load_persisted_authorities(
        self,
        *,
        message_uuid: str,
        processing_run_uuid: str,
    ) -> tuple[PersistedUnitAuthority, ...]:
        assert message_uuid == self.scope.message_uuid
        assert processing_run_uuid == "00000000-0000-4000-8000-000000000100"
        second_start = self.scope.raw_text.index("Keep")
        return (
            PersistedUnitAuthority(
                text_unit_uuid="00000000-0000-4000-8000-000000000102",
                raw_start=0,
                raw_end=second_start - 1,
                annotation_uuid="00000000-0000-4000-8000-000000000104",
                gate_decision_uuid="00000000-0000-4000-8000-000000000105",
                gate_decision="discard",
                route_uuid="00000000-0000-4000-8000-000000000106",
                route_type="ignore",
                route_status="ignored",
                privacy_ceiling="normal",
                privacy_storage_allowed=True,
            ),
            PersistedUnitAuthority(
                text_unit_uuid="00000000-0000-4000-8000-000000000103",
                raw_start=second_start,
                raw_end=len(self.scope.raw_text),
                annotation_uuid="00000000-0000-4000-8000-000000000107",
                gate_decision_uuid="00000000-0000-4000-8000-000000000108",
                gate_decision="analyze",
                route_uuid="00000000-0000-4000-8000-000000000109",
                route_type="project_context",
                route_status="ready",
                privacy_ceiling="normal",
                privacy_storage_allowed=True,
            ),
        )


def _scope(*, raw_text: str) -> CurrentContextScope:
    return CurrentContextScope(
        message_uuid="00000000-0000-4000-8000-000000000110",
        message_version_uuid="00000000-0000-4000-8000-000000000111",
        session_uuid="session-1",
        workspace_uuid="workspace-1",
        project_uuid="project-1",
        user_uuid="user-1",
        actor_workspace_uuid="workspace-1",
        role="user",
        turn_index=10,
        raw_text=raw_text,
    )


def _record(
    *,
    turn: int,
    unit: int,
    text: str,
    session_uuid: str = "session-1",
    role: str = "user",
    visibility: str = "visible",
    is_deleted: bool = False,
    redaction_status: str = "none",
    version_raw_text: str | None = None,
) -> PriorContextRecord:
    message_uuid = f"message-{turn}"
    return PriorContextRecord(
        user_uuid="user-1",
        session_uuid=session_uuid,
        workspace_uuid="workspace-1",
        project_uuid="project-1",
        message_uuid=message_uuid,
        message_version_uuid=f"version-{turn}",
        version_raw_text=text if version_raw_text is None else version_raw_text,
        role=role,
        turn_index=turn,
        visibility=visibility,
        is_deleted=is_deleted,
        redaction_status=redaction_status,
        text_unit_uuid=f"text-unit-{turn}-{unit}",
        unit_index=unit,
        raw_start=0,
        raw_end=len(text),
        unit_text=text,
    )


def _process_message_source(source: str) -> str:
    start = source.index("    def process_message(")
    tail = source[start:]
    next_method = tail.find("\n    def ", len("\n    def "))
    return tail if next_method < 0 else tail[:next_method]


def _stub_semantic_provider(
    monkeypatch: pytest.MonkeyPatch,
    *,
    units: list[dict[str, Any]],
) -> None:
    def execute(**_: Any) -> ContractExecutionOutcome:
        return ContractExecutionOutcome(
            output={
                "schema_version": "1.0",
                "prompt_id": "memorist.semantic_candidate_analysis",
                "prompt_version": "1.0",
                "status": "ok" if units else "abstain",
                "warnings": [],
                "semantic_units": units,
                "references": [],
                "relations": [],
            },
            status="ok" if units else "abstained",
            called_provider=True,
            provider_output_valid=True,
            canonicalized=False,
            repair_attempted=False,
            repair_succeeded=False,
            fallback_used=False,
            fallback_reason=None,
            capability_mode="json_schema",
            provider_response_id="response-1",
            input_tokens=100,
            output_tokens=50,
            latency_ms=25,
            parse_status="parsed",
            attempt_count=1,
            validation_error_paths=[],
        )

    monkeypatch.setattr(
        "memcore.memory_worker.semantic.orchestration.execute_semantic_candidate_contract",
        execute,
    )
