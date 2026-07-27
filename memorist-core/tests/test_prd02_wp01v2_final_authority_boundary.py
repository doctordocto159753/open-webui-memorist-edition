"""Final WP01 authority boundary after the architectural correction."""

from __future__ import annotations

from pathlib import Path

import pytest

from memcore.memory_worker.analysis.modality import (
    NON_AUTHORITATIVE_LEXICAL_HINT,
    VALIDATED_MODEL_ANALYSIS,
    modality_payload,
)
from memcore.memory_worker.consolidation.conflicts import polarities_contradict
from memcore.memory_worker.semantic.candidate_service import read_modality_polarity
from memcore.textsemantics import (
    BASELINE_SEMANTIC_HISTORY_UNITS,
    EXPANDED_SEMANTIC_HISTORY_UNITS,
    Polarity,
    Violation,
    detect_context_dependency,
    semantic_history_window_size,
    validate_semantic_analysis,
    validate_semantic_evidence,
)


def test_deterministic_modality_is_a_hint_not_candidate_authority() -> None:
    payload = modality_payload("We never deploy on Friday.")

    assert payload["polarity"] == "negated"
    assert payload["semantic_authority"] == NON_AUTHORITATIVE_LEXICAL_HINT
    assert read_modality_polarity(payload) is Polarity.UNKNOWN


def test_only_validated_model_is_fresh_polarity_authority() -> None:
    assert (
        read_modality_polarity(
            {
                "semantic_authority": VALIDATED_MODEL_ANALYSIS,
                "polarity": "negated",
            }
        )
        is Polarity.NEGATED
    )
    assert read_modality_polarity({"polarity": "affirmed"}) is Polarity.UNKNOWN
    assert read_modality_polarity({"negated": True}) is Polarity.UNKNOWN
    assert (
        read_modality_polarity({"semantic_authority": "unknown_source", "polarity": "negated"})
        is Polarity.UNKNOWN
    )


def test_legacy_unstamped_rows_require_an_explicit_audit_path() -> None:
    assert (
        read_modality_polarity({"polarity": "affirmed"}, allow_legacy_unstamped=True)
        is Polarity.AFFIRMED
    )
    assert (
        read_modality_polarity({"negated": True}, allow_legacy_unstamped=True)
        is Polarity.NEGATED
    )


def test_production_consolidation_compares_persisted_polarity() -> None:
    assert polarities_contradict(Polarity.AFFIRMED, Polarity.NEGATED)
    assert not polarities_contradict(Polarity.UNKNOWN, Polarity.NEGATED)
    assert not polarities_contradict(Polarity.AFFIRMED, Polarity.AFFIRMED)


def _reference_payload(candidate_unit_ids: object) -> tuple[str, dict[str, object]]:
    raw = "Alpha. Beta. This."
    payload: dict[str, object] = {
        "semantic_units": [
            {"id": "u1", "raw_start": 0, "raw_end": 6, "evidence": raw[0:6]},
            {"id": "u2", "raw_start": 7, "raw_end": 12, "evidence": raw[7:12]},
        ],
        "references": [
            {
                "marker_start": 13,
                "marker_end": 18,
                "marker_evidence": raw[13:18],
                "status": "resolved",
                "target_unit_id": "u1",
                "candidate_unit_ids": candidate_unit_ids,
            }
        ],
    }
    return raw, payload


@pytest.mark.parametrize("candidate_ids", [[], None, "u1"])
def test_resolved_reference_requires_a_nonempty_candidate_list(candidate_ids: object) -> None:
    raw, payload = _reference_payload(candidate_ids)
    report = validate_semantic_evidence(raw, payload)

    assert report.accepted_reference_indexes == ()
    assert report.rejections[-1].violation is Violation.CANDIDATE_LIST_REQUIRED


def test_every_reference_candidate_must_be_an_accepted_unit() -> None:
    raw, payload = _reference_payload(["u1", "missing"])
    report = validate_semantic_evidence(raw, payload)

    assert report.accepted_reference_indexes == ()
    assert report.rejections[-1].violation is Violation.UNKNOWN_CANDIDATE_UNIT_ID


def test_resolved_reference_must_select_from_its_valid_candidates() -> None:
    raw, payload = _reference_payload(["u2"])
    report = validate_semantic_evidence(raw, payload)

    assert report.rejections[-1].violation is Violation.REFERENT_NOT_A_CANDIDATE


def test_valid_resolved_reference_is_accepted() -> None:
    raw, payload = _reference_payload(["u1", "u2"])
    report = validate_semantic_evidence(raw, payload)

    assert report.ok
    assert report.accepted_reference_indexes == (0,)
    # Old callers remain compatible while the name now tells the truth.
    assert validate_semantic_analysis(raw, payload) == report


def test_history_hints_expand_but_never_create_the_baseline() -> None:
    assert semantic_history_window_size(()) == BASELINE_SEMANTIC_HISTORY_UNITS
    hints = detect_context_dependency("This is the layer we discussed.")
    assert hints
    assert semantic_history_window_size(hints) == EXPANDED_SEMANTIC_HISTORY_UNITS
    assert BASELINE_SEMANTIC_HISTORY_UNITS >= 1

    with pytest.raises(ValueError):
        semantic_history_window_size((), baseline_units=0)
    with pytest.raises(ValueError):
        semantic_history_window_size(hints, baseline_units=4, expanded_units=3)


def test_runtime_semantic_authority_does_not_call_lexical_polarity() -> None:
    """Lexical polarity may survive only as a diagnostic hint adapter."""

    root = Path(__file__).resolve().parents[1] / "src" / "memcore"
    allowed = {
        root / "textsemantics" / "polarity.py",
        root / "textsemantics" / "__init__.py",
        root / "memory_worker" / "analysis" / "modality.py",
        root / "memory_worker" / "consolidation" / "conflicts.py",
    }
    offenders: list[Path] = []
    for path in root.rglob("*.py"):
        if path in allowed:
            continue
        text = path.read_text(encoding="utf-8")
        if "extract_polarity(" in text or "is_hypothetical(" in text:
            offenders.append(path)

    assert offenders == [], f"lexical semantic hints used as runtime authority: {offenders}"


def test_resolver_uses_stored_polarity_not_text_diagnostics() -> None:
    root = Path(__file__).resolve().parents[1] / "src" / "memcore"
    resolver = (root / "memory_worker" / "consolidation" / "resolver.py").read_text(
        encoding="utf-8"
    )

    assert "polarities_contradict(current_version.polarity, candidate.polarity)" in resolver
    assert "appears_contradictory(" not in resolver
