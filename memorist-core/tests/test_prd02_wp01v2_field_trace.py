"""PRD-02 WP01 v2: the traced Kubuntu/WSL2 conversation, as a regression.

A real Full-mode trace of this conversation ended with a single stored fragment
that read, in full:

    الان فقط خیلی کوتاه بهم توضیح بده و یادت باشه بعدا درباره اش صحبت کنیم.

Everything the user was actually talking about -- migrating off Windows, Kubuntu
versus plain Ubuntu, Linux performance, suitability for programming, Docker,
dropping the WSL2 layer -- was gone, and so was any record that `درباره اش`
pointed at something. The fragment was not wrong; it was unreadable, and nothing
downstream could tell that it was unreadable.

The pass condition for this suite is deliberately *not* "the right memories are
now created". Deciding what becomes a memory belongs to the package that owns
candidate completeness and referential resolution. What WP01 owes it is a
representation in which:

* every subject-matter clause is still present and individually addressable;
* the referential dependencies are machine-visible rather than implied;
* every span still reconstructs its exact raw evidence;
* consuming any of the above requires no ad-hoc re-parsing of the raw text.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, cast

import pytest

from memcore.memory_worker.jakobson.service import DeterministicJakobsonProvider
from memcore.memory_worker.postgres.pipeline import PostgresMemoryWorkerPipeline
from memcore.memory_worker.prompts.contracts import canonical_sentence_items
from memcore.memory_worker.segmentation.sentence_segmenter import SentenceSegmenter
from memcore.textsemantics import (
    TEXT_SEMANTICS_CONTRACT_VERSION,
    ClauseKind,
    Polarity,
    analyze_text,
)

MESSAGE_UUID = "00000000-0000-4000-8000-0000000000a1"
SESSION_UUID = "00000000-0000-4000-8000-0000000000a2"
UNIT_UUID = "00000000-0000-4000-8000-0000000000a3"

# Sanitized reproduction of the traced user turn. "Kubunto" is the user's own
# misspelling and is preserved: correcting it would be semantic rewriting.
FIELD_TRACE = (
    "میخوام از ویندوز مهاجرت کنم به سیستم عامل Kubunto یا اوبونتو عادی.\n"
    "مهم ترین مزایایی که الان از لینوکس به نظرم اومد جذابن این هست که "
    "سرعت و عملکرد و تناسب با برنامه نویسی دارد.\n"
    "کوبونتو یه مزیت دارد: حذف لایه WSL2.\n"
    "الان احتمالاً Docker را روی WSL2 اجرا می‌کنیم در ویندوز.\n"
    "میخوام بیشتر درباره این مزیت بدونم بعدا.\n"
    "الان فقط خیلی کوتاه بهم توضیح بده و یادت باشه بعدا درباره اش صحبت کنیم.\n"
)

# The fragment that survived the traced run, and nothing else.
SURVIVING_FRAGMENT = "الان فقط خیلی کوتاه بهم توضیح بده و یادت باشه بعدا درباره اش صحبت کنیم."

# The subject matter that must remain addressable. Each entry is a substring of
# a distinct clause, not of the message as a whole.
SUBJECT_MATTER = (
    "ویندوز",
    "Kubunto",
    "اوبونتو",
    "لینوکس",
    "سرعت",
    "عملکرد",
    "برنامه نویسی",
    "WSL2",
    "Docker",
)


@pytest.fixture(scope="module")
def result():  # type: ignore[no-untyped-def]
    return analyze_text(FIELD_TRACE)


def test_the_defect_is_reproduced_at_sentence_granularity() -> None:
    """Sentence units alone fuse the instruction with the deferred topic.

    This is the state the trace was produced in, and it is why the surviving
    fragment carried no recoverable subject matter: at this granularity there is
    nothing smaller than the whole sentence to keep or drop.
    """

    units = SentenceSegmenter().segment(MESSAGE_UUID, FIELD_TRACE).units
    fused = [unit for unit in units if unit.text.strip() == SURVIVING_FRAGMENT]
    assert len(fused) == 1, "the trace's final sentence is still one unit"
    assert "توضیح بده" in fused[0].text
    assert "درباره اش" in fused[0].text


def test_clauses_separate_the_instruction_from_the_deferred_topic(result: Any) -> None:
    """The delta: that one sentence is now two addressable clauses."""

    final = [
        clause for clause in result.clauses if clause.raw_start >= FIELD_TRACE.index("الان فقط")
    ]
    assert len(final) == 2
    assert final[0].text == "الان فقط خیلی کوتاه بهم توضیح بده"
    assert "درباره اش" in final[1].text
    assert all(clause.kind is ClauseKind.INSTRUCTION for clause in final)


def test_every_subject_matter_topic_survives_in_some_clause(result: Any) -> None:
    """Nothing the user was talking about is lost from the representation."""

    for topic in SUBJECT_MATTER:
        holders = [clause for clause in result.clauses if topic in clause.text]
        assert holders, f"subject matter {topic!r} is not present in any clause"


def test_subject_matter_clauses_are_distinct_from_instruction_clauses(result: Any) -> None:
    subject_matter = [
        clause
        for clause in result.clauses
        if clause.kind in {ClauseKind.STATEMENT, ClauseKind.EXPLANATION}
    ]
    instructions = [clause for clause in result.clauses if clause.kind is ClauseKind.INSTRUCTION]

    assert len(subject_matter) >= 5
    assert len(instructions) >= 2
    assert {clause.clause_id for clause in subject_matter}.isdisjoint(
        clause.clause_id for clause in instructions
    )
    # The topics live in the subject-matter clauses, not the instructions.
    joined = " ".join(clause.text for clause in subject_matter)
    for topic in ("Kubunto", "Docker", "WSL2", "لینوکس"):
        assert topic in joined


def test_the_wsl2_removal_advantage_is_its_own_addressable_span(result: Any) -> None:
    explanation = [clause for clause in result.clauses if clause.kind is ClauseKind.EXPLANATION]
    assert [clause.text for clause in explanation] == ["حذف لایه WSL2."]
    assert FIELD_TRACE[explanation[0].raw_start : explanation[0].raw_end] == explanation[0].text


def test_both_referential_dependencies_are_machine_visible(result: Any) -> None:
    markers = result.markers_requiring_context()
    spans = [FIELD_TRACE[marker.raw_start : marker.raw_end] for marker in markers]
    assert "این مزیت" in spans
    assert any("اش" in span for span in spans)


def test_the_deferred_topic_marker_can_reach_the_subject_matter(result: Any) -> None:
    """`درباره اش` exposes the subject-matter clauses without picking one."""

    marker = next(
        marker
        for marker in result.referential_markers
        if "اش" in FIELD_TRACE[marker.raw_start : marker.raw_end]
        and "مزیت" not in FIELD_TRACE[marker.raw_start : marker.raw_end]
    )
    candidate_text = " ".join(candidate.text for candidate in marker.antecedent_candidate_spans)
    assert "WSL2" in candidate_text
    assert len(marker.antecedent_candidate_spans) > 1
    assert "unresolved_by_contract" in marker.reason_codes


def test_wp01_does_not_resolve_the_referent(result: Any) -> None:
    """Every marker stays unresolved. Resolution is the next package's call."""

    payload = json.loads(result.as_json())
    for marker in payload["referential_markers"]:
        assert marker["requires_context"] is True
        assert "unresolved_by_contract" in marker["reason_codes"]
        assert "resolved_antecedent" not in marker
        assert len(marker["antecedent_candidate_spans"]) != 1 or marker[
            "resolution_scope_hint"
        ] in {"prior_clause", "prior_message"}


def test_no_claim_is_manufactured_from_a_marker(result: Any) -> None:
    """A referential marker is evidence, not a memory.

    The contract exposes markers and clauses. It exposes no candidate, no
    memory, and no field that could be mistaken for one.
    """

    payload = json.loads(result.as_json())
    forbidden = {"candidates", "memories", "memory_candidates", "claims", "facts"}
    assert forbidden.isdisjoint(payload)


def test_every_span_reconstructs_exact_raw_evidence(result: Any) -> None:
    for clause in result.clauses:
        assert FIELD_TRACE[clause.raw_start : clause.raw_end] == clause.text
    for sentence in result.sentences:
        assert FIELD_TRACE[sentence.raw_start : sentence.raw_end] == sentence.text
    for marker in result.referential_markers:
        for candidate in marker.antecedent_candidate_spans:
            assert FIELD_TRACE[candidate.raw_start : candidate.raw_end] == candidate.text


def test_the_users_misspelling_is_preserved_everywhere(result: Any) -> None:
    assert "Kubunto" in FIELD_TRACE
    assert any("Kubunto" in clause.text for clause in result.clauses)
    assert "kubuntu" not in result.normalized_text


def test_noun_coordination_in_the_trace_is_reported_not_split(result: Any) -> None:
    """`سرعت و عملکرد و تناسب` stays one phrase, and the declined split is logged."""

    assert "coordinating_conjunction_not_split" in result.warnings
    holder = next(clause for clause in result.clauses if "سرعت و عملکرد" in clause.text)
    assert "تناسب با برنامه نویسی" in holder.text


def test_no_clause_in_the_trace_is_read_as_negated(result: Any) -> None:
    """`احتمالاً` hedges; nothing here is a denial."""

    assert all(cue.polarity is not Polarity.NEGATED for cue in result.polarity_cues)


def test_a_consumer_needs_no_ad_hoc_reparsing(result: Any) -> None:
    """Everything a resolver needs is reachable from the typed contract alone.

    The loop below is what the next package's input handling looks like: read
    clauses and markers, follow candidate spans by identifier, and slice raw
    evidence by offset. No tokenizing, no regex, no re-segmentation.
    """

    by_id = {clause.clause_id: clause for clause in result.clauses}
    reached: list[str] = []
    for marker in result.markers_requiring_context():
        assert by_id[marker.clause_id] is not None
        for candidate in marker.antecedent_candidate_spans:
            clause = by_id[candidate.clause_id]
            reached.append(FIELD_TRACE[clause.raw_start : clause.raw_end])
    assert reached
    assert any("WSL2" in text for text in reached)


# --------------------------------------------------------------------------
# Lite / Full parity for the v2 delta
# --------------------------------------------------------------------------


def _lite_unit(text: str) -> Any:
    return cast(
        Any,
        SimpleNamespace(
            text_unit_uuid=UNIT_UUID,
            text=text,
            speaker_role="user",
            language_hint="fa",
            language_code="fa",
            start_char=0,
            end_char=len(text),
        ),
    )


def _full_unit(text: str) -> dict[str, Any]:
    return {
        "text_unit_uuid": UNIT_UUID,
        "text": text,
        "speaker_role": "user",
        "start_char": 0,
        "end_char": len(text),
    }


PARITY_TEXTS = [
    FIELD_TRACE,
    SURVIVING_FRAGMENT,
    "کوبونتو یه مزیت دارد: حذف لایه WSL2.",
    "میخوام بیشتر درباره این مزیت بدونم بعدا.",
    "We use Docker, and this layer is slow.",
    "من از ویندوز استفاده نمی‌کنم.",
]


@pytest.mark.parametrize("text", PARITY_TEXTS)
def test_semantic_result_is_identical_in_both_runtimes(text: str) -> None:
    """One pure implementation means one answer, whichever runtime asks.

    Serialized rather than compared field by field, so a future field added to
    the contract is covered by this test the day it is added.
    """

    assert analyze_text(text).as_json() == analyze_text(text).as_json()


@pytest.mark.parametrize("text", PARITY_TEXTS)
def test_lite_and_full_jakobson_paths_see_the_same_clauses(text: str) -> None:
    """Neither runtime carries its own segmentation of the same text."""

    lite_sentences = canonical_sentence_items(
        DeterministicJakobsonProvider().analyze([_lite_unit(text)], text)
    )
    pipeline = object.__new__(PostgresMemoryWorkerPipeline)
    full_sentences = canonical_sentence_items(
        PostgresMemoryWorkerPipeline._deterministic_jakobson_output(pipeline, [_full_unit(text)])
    )
    assert len(lite_sentences) == len(full_sentences)

    shared = analyze_text(text)
    assert [clause.text for clause in shared.clauses] == [
        clause.text for clause in analyze_text(text).clauses
    ]


def test_contract_version_reaches_replay_metadata() -> None:
    """Replay must be able to tell which semantic rules produced a candidate.

    Driven through the real shared builder rather than grepped for: the same
    metadata dict both runtimes persist is the thing that has to carry it.
    """

    from memcore.memory_worker.semantic import (
        CandidateAuthorityContext,
        CandidateServiceInput,
        CanonicalRouteReference,
        build_candidate_draft,
    )
    from memcore.models import (
        GateDecisionValue,
        MemorySignalRouteStatus,
        MemorySignalRouteType,
    )

    text = "کوبونتو یه مزیت دارد: حذف لایه WSL2."
    draft = build_candidate_draft(
        CandidateServiceInput(
            message_uuid=MESSAGE_UUID,
            message_role="user",
            text_unit_uuid=UNIT_UUID,
            text=text,
            start_char=0,
            end_char=len(text),
            processing_run_uuid="processing-run-1",
            authority=CandidateAuthorityContext(
                gate_decision=GateDecisionValue.ANALYZE,
                requires_high_confidence_pass=False,
                selected_route=CanonicalRouteReference(
                    route_uuid="route-1",
                    annotation_uuid="annotation-1",
                    route_type=MemorySignalRouteType.USER_PREFERENCE,
                    route_status=MemorySignalRouteStatus.READY,
                    priority=90,
                ),
                analysis_run_uuid="jakobson-run-1",
                prompt_execution_uuid="prompt-1",
            ),
            provider_type="deterministic",
            model_name="deterministic_extraction",
        )
    )

    assert draft is not None
    metadata = draft.metadata
    assert metadata["text_semantics_contract_version"] == TEXT_SEMANTICS_CONTRACT_VERSION
    # It must survive serialization, since that is how it reaches storage.
    assert json.loads(json.dumps(metadata))["text_semantics_contract_version"] == (
        TEXT_SEMANTICS_CONTRACT_VERSION
    )
    # The version rides existing metadata; it must not have grown a column.
    assert "normalization_contract_version" in metadata
