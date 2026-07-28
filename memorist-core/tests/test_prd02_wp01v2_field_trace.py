"""PRD-02 WP01 v2: the traced Kubuntu/WSL2 conversation, under the corrected split.

A real Full-mode trace of this conversation stored one fragment and lost every
topic it referred to:

    الان فقط خیلی کوتاه بهم توضیح بده و یادت باشه بعدا درباره اش صحبت کنیم.

The first attempt at fixing this built a rule-based clause splitter and
reference resolver in deterministic code. It got the traced example right and
was wrong in a new way for every example after it, because deciding what a
sentence asserts is not something a lexicon of verb forms can do.

So the pass condition here changed. WP01 no longer claims to recover the subject
matter -- that is the model-equipped semantic node's job. WP01 must show that:

* the envelope preserves the raw text and the exact offsets the model will cite;
* nothing in the message is discarded before the model sees it;
* the deictics that make the fragment incomplete are *flagged*, so the router
  knows to send conversation context -- without claiming what they refer to;
* a faithful model analysis of this text validates;
* an unfaithful one is rejected rather than repaired.

The authority chain under test:

    deterministic envelope -> model semantic analysis -> validation -> WP02
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, cast

import pytest

from memcore.memory_worker.jakobson.service import DeterministicJakobsonProvider
from memcore.memory_worker.postgres.pipeline import PostgresMemoryWorkerPipeline
from memcore.memory_worker.prompts.contracts import canonical_sentence_items
from memcore.textsemantics import (
    TEXT_SEMANTICS_CONTRACT_VERSION,
    SemanticFallback,
    Violation,
    build_envelope,
    validate_semantic_analysis,
)

MESSAGE_UUID = "00000000-0000-4000-8000-0000000000a1"
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

SURVIVING_FRAGMENT = "الان فقط خیلی کوتاه بهم توضیح بده و یادت باشه بعدا درباره اش صحبت کنیم."

# Every topic the user actually raised. The envelope must carry all of it
# forward; it does not have to understand any of it.
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
def envelope():  # type: ignore[no-untyped-def]
    return build_envelope(FIELD_TRACE)


def test_nothing_is_discarded_before_the_model_sees_it(envelope: Any) -> None:
    """The whole message survives, verbatim, addressable by offset."""

    reassembled = "".join(FIELD_TRACE[span.raw_start : span.raw_end] for span in envelope.sentences)
    for topic in SUBJECT_MATTER:
        assert topic in reassembled, f"{topic!r} was dropped before analysis"


def test_the_traced_fragment_is_still_present_and_addressable(envelope: Any) -> None:
    holder = [span for span in envelope.sentences if SURVIVING_FRAGMENT in span.text]
    assert holder
    assert FIELD_TRACE[holder[0].raw_start : holder[0].raw_end] == holder[0].text


def test_the_deictics_that_made_the_fragment_incomplete_are_flagged(envelope: Any) -> None:
    """`این` and `اش` are why the stored fragment was unreadable.

    Flagging them is a routing signal -- send conversation context to the model.
    It is deliberately not a claim about what they refer to.
    """

    evidence = [hint.evidence for hint in envelope.context_dependency_hints]
    assert "این" in evidence
    assert "اش" in evidence
    assert envelope.requires_conversation_context is True


def test_wp01_makes_no_claim_about_what_the_deictics_refer_to(envelope: Any) -> None:
    payload = json.loads(envelope.as_json())
    assert payload["context_dependency_hints"]
    for hint in payload["context_dependency_hints"]:
        assert hint["authority"] == "non_authoritative"
        assert "target" not in hint
        assert "candidates" not in hint
        assert "referent" not in hint


def test_the_users_misspelling_is_preserved(envelope: Any) -> None:
    assert "Kubunto" in FIELD_TRACE
    assert any("Kubunto" in span.text for span in envelope.sentences)
    assert "kubuntu" not in envelope.normalized_text


def test_noun_coordination_is_never_split_by_deterministic_code(envelope: Any) -> None:
    """`سرعت و عملکرد و تناسب` stays intact because nothing here reads `و`."""

    holder = [span for span in envelope.sentences if "سرعت و عملکرد" in span.text]
    assert holder
    assert "تناسب با برنامه نویسی" in holder[0].text


def test_soft_wraps_in_the_trace_are_not_treated_as_boundaries() -> None:
    wrapped = "میخوام بیشتر درباره\nاین مزیت بدونم بعدا."
    result = build_envelope(wrapped)
    assert [span.text for span in result.sentences] == [wrapped]
    assert "line_break_not_a_boundary" in result.warnings


# --------------------------------------------------------------------------
# The model's answer, and the deterministic verdict on it
# --------------------------------------------------------------------------


def _model_analysis() -> dict[str, Any]:
    """A faithful analysis of the trace, of the shape the model must return.

    This is what WP02 consumes. Note what it contains that no rule engine here
    produces: a proposition in the user's own terms, durability, and a resolved
    reference -- each anchored to an exact slice of the raw message.
    """

    advantage = FIELD_TRACE.index("حذف لایه WSL2.")
    marker = FIELD_TRACE.index("این مزیت")
    return {
        "semantic_units": [
            {
                "id": "u1",
                "raw_start": advantage,
                "raw_end": advantage + len("حذف لایه WSL2."),
                "evidence": "حذف لایه WSL2.",
                "proposition": "Kubuntu's advantage is removing the WSL2 layer.",
                "unit_type": "preference",
                "polarity": "affirmed",
                "durability": "durable",
            }
        ],
        "references": [
            {
                "marker_start": marker,
                "marker_end": marker + len("این مزیت"),
                "marker_evidence": "این مزیت",
                "status": "resolved",
                "target_unit_id": "u1",
                "candidate_unit_ids": ["u1"],
                "confidence": "high",
            }
        ],
    }


def test_a_faithful_model_analysis_of_the_trace_validates() -> None:
    report = validate_semantic_analysis(FIELD_TRACE, _model_analysis())
    assert report.ok
    assert report.accepted_unit_ids == ("u1",)
    assert report.accepted_reference_indexes == (0,)
    assert report.fallback is None


def test_a_model_that_paraphrases_the_evidence_is_rejected() -> None:
    """The proposition may be a paraphrase. The evidence may not be."""

    payload = _model_analysis()
    payload["semantic_units"][0]["evidence"] = "removing the WSL2 layer"
    report = validate_semantic_analysis(FIELD_TRACE, payload)
    assert report.rejections[0].violation is Violation.EVIDENCE_NOT_A_SLICE
    assert report.fallback is SemanticFallback.RETAIN_RAW_ONLY


def test_a_model_that_invents_a_span_is_rejected() -> None:
    payload = _model_analysis()
    payload["semantic_units"][0]["raw_end"] = len(FIELD_TRACE) + 500
    report = validate_semantic_analysis(FIELD_TRACE, payload)
    assert report.rejections[0].violation is Violation.SPAN_OUT_OF_RANGE


def test_a_model_that_resolves_to_a_unit_it_never_proposed_is_rejected() -> None:
    payload = _model_analysis()
    payload["references"][0]["target_unit_id"] = "u-does-not-exist"
    report = validate_semantic_analysis(FIELD_TRACE, payload)
    assert report.rejections[0].violation is Violation.UNKNOWN_UNIT_ID


def test_without_a_model_the_pipeline_abstains_rather_than_guessing() -> None:
    """The whole point of the correction.

    When no semantic analysis exists, deterministic code does not reconstruct
    meaning from hand-written rules. It declines.
    """

    assert validate_semantic_analysis(FIELD_TRACE, {}).fallback is SemanticFallback.ABSTAIN


def test_offsets_the_model_cites_address_the_text_an_auditor_reads() -> None:
    """The envelope and the model's citation agree character for character."""

    analysis = _model_analysis()
    for unit in analysis["semantic_units"]:
        assert FIELD_TRACE[unit["raw_start"] : unit["raw_end"]] == unit["evidence"]
    for reference in analysis["references"]:
        cited = FIELD_TRACE[reference["marker_start"] : reference["marker_end"]]
        assert cited == reference["marker_evidence"]


# --------------------------------------------------------------------------
# Lite / Full parity
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
    "We use Docker, and this layer is slow.",
    "من از ویندوز استفاده نمی‌کنم.",
]


@pytest.mark.parametrize("text", PARITY_TEXTS)
def test_envelope_replay_is_byte_identical(text: str) -> None:
    """Determinism only -- both calls run the same pure function in-process.

    Named for what it proves. Comparing a pure function to itself cannot detect
    Lite/Full drift, and a test that looks like it does is worse than none.
    """

    assert build_envelope(text).as_json() == build_envelope(text).as_json()


@pytest.mark.parametrize("text", PARITY_TEXTS)
def test_lite_and_full_deterministic_paths_agree_on_the_same_text(text: str) -> None:
    """A real cross-runtime comparison: two implementations, two results."""

    lite = canonical_sentence_items(
        DeterministicJakobsonProvider().analyze([_lite_unit(text)], text)
    )
    pipeline = object.__new__(PostgresMemoryWorkerPipeline)
    full = canonical_sentence_items(
        PostgresMemoryWorkerPipeline._deterministic_jakobson_output(pipeline, [_full_unit(text)])
    )

    assert len(lite) == len(full)
    for lite_item, full_item in zip(lite, full, strict=True):
        assert lite_item["text"] == full_item["text"]
        assert lite_item["dominant_function"] == full_item["dominant_function"]
        assert lite_item["secondary_functions"] == full_item["secondary_functions"]
        assert (
            lite_item["six_factors"]["context_referent"]
            == full_item["six_factors"]["context_referent"]
        )
        # The envelope over each runtime's own text. Because the two texts came
        # from different code paths, a second segmentation authority in either
        # runtime shows up right here.
        assert build_envelope(lite_item["text"]).as_json() == (
            build_envelope(full_item["text"]).as_json()
        )


def test_no_runtime_branch_exists_inside_the_semantics_package() -> None:
    """Drift is prevented structurally, not merely observed to be absent."""

    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "src" / "memcore" / "textsemantics"
    modules = sorted(root.glob("*.py"))
    assert len(modules) >= 8, "glob found nothing; this test would pass vacuously"
    forbidden = ("runtime_profile", "canonical_store", "postgres", "sqlite", "Settings", "getenv")
    for module in modules:
        source = module.read_text(encoding="utf-8")
        for needle in forbidden:
            assert needle not in source, f"{module.name} branches on runtime via {needle!r}"


def test_contract_version_reaches_replay_metadata() -> None:
    """Replay must be able to tell which envelope rules produced a candidate."""

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
    assert draft.metadata["text_semantics_contract_version"] == TEXT_SEMANTICS_CONTRACT_VERSION
