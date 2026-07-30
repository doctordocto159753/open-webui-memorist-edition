"""PRD-02 WP01 v2: the deterministic envelope, and the boundary it enforces.

These tests exist to hold a line that an earlier version of this package
crossed. That version tried to decide, in hand-written Python, where a
proposition ended, whether ``و`` joined clauses or nouns, whether a clause was
an instruction or a statement, and which earlier span a pronoun pointed at.
Every new example needed another lexicon entry and produced another confident
mistake.

The line is: **deterministic code enforces truth boundaries; the model analyses
language.** So the assertions below come in two kinds --

* the envelope reports only what a person typed: offsets, punctuation, code
  fences, tokens, scripts;
* the validator rejects a model answer that does not match the raw text.

There is also a set of tests asserting what is *absent*, because the absence is
the architectural guarantee.
"""

from __future__ import annotations

import json
import random

import pytest

from memcore.textsemantics import (
    NON_AUTHORITATIVE,
    TEXT_SEMANTICS_CONTRACT_VERSION,
    SemanticFallback,
    TextEnvelope,
    Violation,
    build_envelope,
    detect_context_dependency,
    identifier_phrases,
    normalize_with_mapping,
    normalized_span_for_raw_span,
    raw_span_for_normalized_span,
    segment_sentences,
    validate_semantic_analysis,
)

FIELD_TRACE = (
    "میخوام از ویندوز مهاجرت کنم به سیستم عامل Kubunto یا اوبونتو عادی.\n"
    "مهم ترین مزایایی که الان از لینوکس به نظرم اومد جذابن این هست که "
    "سرعت و عملکرد و تناسب با برنامه نویسی دارد.\n"
    "کوبونتو یه مزیت دارد: حذف لایه WSL2.\n"
    "الان احتمالاً Docker را روی WSL2 اجرا می‌کنیم در ویندوز.\n"
    "میخوام بیشتر درباره این مزیت بدونم بعدا.\n"
    "الان فقط خیلی کوتاه بهم توضیح بده و یادت باشه بعدا درباره اش صحبت کنیم.\n"
)


# --------------------------------------------------------------------------
# What the envelope must NOT contain
# --------------------------------------------------------------------------


def test_envelope_carries_no_semantic_judgement() -> None:
    """The absence of these fields is the architectural guarantee.

    Each one was present in an earlier version and each was produced by rules
    that could not justify themselves. A field that cannot exist cannot be
    quietly trusted by a downstream package.
    """

    envelope = build_envelope(FIELD_TRACE)
    forbidden = {
        "clauses",
        "clause_kinds",
        "propositions",
        "referential_markers",
        "antecedent_candidate_spans",
        "polarity_cues",
        "instructions",
        "statements",
        "referent",
        "resolved_antecedent",
    }
    assert forbidden.isdisjoint(vars(envelope))
    assert forbidden.isdisjoint(json.loads(envelope.as_json()))


def test_no_syntactic_lexicon_survives_in_the_package() -> None:
    """No verb, conjunction, or imperative list may drive segmentation again.

    A sampled behavioural test cannot prove a lexicon is gone; naming the
    symbols does. These are the exact names that grew by one entry per failing
    example.
    """

    from memcore.textsemantics import segmentation

    for symbol in (
        "CLAUSE_FINAL_VERBS",
        "COORDINATING_CONJUNCTIONS",
        "CONTRASTIVE_CONNECTIVES",
        "IMPERATIVE_MARKERS",
        "ClauseKind",
        "ClauseSpan",
        "segment_clauses",
    ):
        assert not hasattr(segmentation, symbol), f"{symbol} is back in segmentation"


def test_context_hints_are_stamped_non_authoritative() -> None:
    envelope = build_envelope(FIELD_TRACE)
    assert envelope.context_dependency_hints
    for hint in envelope.context_dependency_hints:
        assert hint.authority == NON_AUTHORITATIVE
        assert hint.is_authoritative is False
    for record in json.loads(envelope.as_json())["context_dependency_hints"]:
        assert record["authority"] == NON_AUTHORITATIVE


def test_no_production_path_builds_memory_from_a_context_hint() -> None:
    """Only the WP02 fail-closed validator may consume the hint."""

    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "src" / "memcore"
    consumers = [
        path
        for path in root.rglob("*.py")
        if "textsemantics" not in path.parts
        and (
            "detect_context_dependency" in path.read_text(encoding="utf-8")
            or "context_dependency_hints" in path.read_text(encoding="utf-8")
        )
    ]
    allowed = root / "memory_worker" / "semantic" / "coverage" / "planner.py"
    assert consumers == [allowed], f"unexpected context-hint consumers: {consumers}"
    planner = allowed.read_text(encoding="utf-8")
    assert "_dependency_hint_without_reference" in planner
    assert "has_unresolved_reference=unresolved" in planner


# --------------------------------------------------------------------------
# The envelope: typography, not syntax
# --------------------------------------------------------------------------


def test_sentences_split_only_on_marks_the_writer_typed() -> None:
    envelope = build_envelope("اول است. دوم است؟ سوم است!")
    assert [span.text for span in envelope.sentences] == ["اول است.", "دوم است؟", "سوم است!"]
    assert all(span.boundary_reason != "line_break" for span in envelope.sentences)


def test_a_soft_wrap_is_not_a_boundary_and_says_so() -> None:
    """The defect that finally showed the layer was wrong.

    Splitting here produced two half-sentences that then looked like two
    propositions. The envelope now leaves the text alone and records that a wrap
    was seen, which is all a transcription layer is entitled to say.
    """

    text = "The deployment pipeline runs on\nFriday afternoons and it is slow."
    envelope = build_envelope(text)
    assert [span.text for span in envelope.sentences] == [text]
    assert "line_break_not_a_boundary" in envelope.warnings


def test_blank_line_and_list_marker_are_explicit_structure() -> None:
    blank = build_envelope("اولی بدون نقطه\n\nدومی بدون نقطه")
    assert len(blank.sentences) == 2
    assert blank.sentences[1].boundary_reason == "blank_line"

    bullets = build_envelope("Steps:\n- install docker\n- remove wsl2")
    assert "- remove wsl2" in [span.text for span in bullets.sentences]
    assert any(span.boundary_reason == "list_item" for span in bullets.sentences)

    numbered = build_envelope("Order:\n1. install docker\n2. remove wsl2")
    assert "2. remove wsl2" in [span.text for span in numbered.sentences]


def test_identifiers_and_decimals_never_end_a_sentence() -> None:
    for text in ("We use GPT-5.4 today.", "Version 1.2.3 shipped.", "See example.com now."):
        assert len(build_envelope(text).sentences) == 1, text


def test_fenced_code_is_its_own_span_and_is_byte_preserved() -> None:
    text = "قبل\n```\nKEY=ABC  ي  ك  this=that\n```\nبعد."
    envelope = build_envelope(text)
    code = [span for span in envelope.sentences if span.is_code]
    assert len(code) == 1
    assert "KEY=ABC  ي  ك  this=that" in code[0].text
    assert text[code[0].raw_start : code[0].raw_end] == code[0].text
    # A demonstrative inside a fence is a variable, not a pronoun.
    assert all(
        hint.raw_start < code[0].raw_start or hint.raw_end > code[0].raw_end
        for hint in envelope.context_dependency_hints
    )


@pytest.mark.parametrize("text", [FIELD_TRACE, "Docker on WSL2.\nتیم آماده است."])
def test_every_span_reconstructs_its_exact_raw_evidence(text: str) -> None:
    envelope = build_envelope(text)
    for span in envelope.sentences:
        assert text[span.raw_start : span.raw_end] == span.text
    for hint in envelope.context_dependency_hints:
        assert text[hint.raw_start : hint.raw_end] == hint.evidence
    for phrase in envelope.phrases:
        assert text[phrase.raw_start : phrase.raw_end] == phrase.raw_text


def test_spans_never_overlap() -> None:
    spans = sorted((span.raw_start, span.raw_end) for span in build_envelope(FIELD_TRACE).sentences)
    for earlier, later in zip(spans, spans[1:], strict=False):
        assert earlier[1] <= later[0]


def test_envelope_is_deterministic_and_json_safe() -> None:
    assert build_envelope(FIELD_TRACE).as_json() == build_envelope(FIELD_TRACE).as_json()
    assert json.loads(build_envelope(FIELD_TRACE).as_json())["contract_version"] == (
        TEXT_SEMANTICS_CONTRACT_VERSION
    )


def test_audit_payload_carries_no_raw_text() -> None:
    secret = "sk-" + "ant-api03-abcdefghijklmnop"
    payload = build_envelope(f"{FIELD_TRACE}\nkey is {secret}").as_json()
    assert "sk-ant-api03" not in payload
    assert "کوبونتو" not in payload


def test_envelope_is_immutable_at_the_api_boundary() -> None:
    envelope = build_envelope("سلام.")
    with pytest.raises(AttributeError):
        envelope.contract_version = "tampered"  # type: ignore[misc]


def test_empty_text_produces_a_valid_empty_envelope() -> None:
    for text in ("", "   ", "\n\n"):
        envelope = build_envelope(text)
        assert envelope.sentences == ()
        assert envelope.context_dependency_hints == ()
        assert json.loads(envelope.as_json())["contract_version"]


def test_identifiers_split_by_tokenization_are_recoverable() -> None:
    phrases = {match.evidence for match in identifier_phrases("set A_B_C and use GPT-5.4")}
    assert "A_B_C" in phrases
    assert "GPT-5.4" in phrases


def test_span_mapping_property_over_generated_mixed_script_text() -> None:
    alphabet = ["ی", "ي", "ک", "ك", "ا", "‌", " ", "\n", "a", "Z", "2", ".", "،", "ً", "-"]
    generator = random.Random(20260727)
    for _ in range(300):
        raw = "".join(generator.choice(alphabet) for _ in range(generator.randint(1, 60)))
        mapped = normalize_with_mapping(raw)
        assert mapped.raw == raw
        for index in range(len(mapped.text)):
            start, end = raw_span_for_normalized_span(mapped, index, index + 1)
            back = normalized_span_for_raw_span(mapped, start, end)
            assert back is not None and back[0] <= index < back[1]
        for span in build_envelope(raw).sentences:
            assert raw[span.raw_start : span.raw_end] == span.text


def test_segment_helper_agrees_with_the_assembled_envelope() -> None:
    spans, _ = segment_sentences(FIELD_TRACE)
    assert [span.text for span in spans] == [
        span.text for span in build_envelope(FIELD_TRACE).sentences
    ]


# --------------------------------------------------------------------------
# The validator: is the model's answer admissible?
# --------------------------------------------------------------------------


def _payload(raw: str, **overrides: object) -> dict[str, object]:
    unit = {
        "id": "u1",
        "raw_start": 0,
        "raw_end": 21,
        "evidence": raw[0:21],
        "proposition": "Kubuntu removes the WSL2 layer.",
    }
    unit.update(overrides)
    return {"semantic_units": [unit], "references": []}


RAW = "کوبونتو یه مزیت دارد: حذف لایه WSL2."


def test_a_faithful_analysis_is_accepted() -> None:
    report = validate_semantic_analysis(RAW, _payload(RAW))
    assert report.ok
    assert report.accepted_unit_ids == ("u1",)
    assert report.fallback is None


def test_paraphrased_evidence_is_rejected() -> None:
    """The proposition may be a paraphrase; the evidence may never be.

    A model that tidies whitespace or drops a ZWNJ has produced text that no
    longer addresses the stored message, so it cannot be persisted as evidence.
    """

    report = validate_semantic_analysis(RAW, _payload(RAW, evidence="Kubuntu has an advantage"))
    assert not report.ok
    assert report.rejections[0].violation is Violation.EVIDENCE_NOT_A_SLICE
    assert report.accepted_unit_ids == ()


def test_evidence_differing_only_by_a_zwnj_is_rejected() -> None:
    raw = "می‌کنیم"
    payload = {
        "semantic_units": [{"id": "u1", "raw_start": 0, "raw_end": len(raw), "evidence": "میکنیم"}]
    }
    report = validate_semantic_analysis(raw, payload)
    assert report.rejections[0].violation is Violation.EVIDENCE_NOT_A_SLICE


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"raw_start": -1}, Violation.SPAN_OUT_OF_RANGE),
        ({"raw_end": 10_000}, Violation.SPAN_OUT_OF_RANGE),
        ({"raw_start": 9, "raw_end": 4}, Violation.SPAN_INVERTED),
        ({"raw_start": "0"}, Violation.MALFORMED_RECORD),
        ({"id": ""}, Violation.MALFORMED_RECORD),
    ],
)
def test_impossible_spans_are_rejected(overrides: dict[str, object], expected: Violation) -> None:
    report = validate_semantic_analysis(RAW, _payload(RAW, **overrides))
    assert report.rejections[0].violation is expected


def test_missing_evidence_is_rejected_rather_than_assumed() -> None:
    payload: dict[str, object] = {"semantic_units": [{"id": "u1", "raw_start": 0, "raw_end": 5}]}
    report = validate_semantic_analysis(RAW, payload)
    assert report.rejections[0].violation is Violation.EVIDENCE_MISSING


def test_overlapping_units_are_rejected() -> None:
    payload = {
        "semantic_units": [
            {"id": "u1", "raw_start": 0, "raw_end": 21, "evidence": RAW[0:21]},
            {"id": "u2", "raw_start": 10, "raw_end": 30, "evidence": RAW[10:30]},
        ]
    }
    report = validate_semantic_analysis(RAW, payload)
    assert report.accepted_unit_ids == ("u1",)
    assert report.rejections[0].violation is Violation.UNITS_OVERLAP


def test_duplicate_unit_ids_are_rejected() -> None:
    payload = {
        "semantic_units": [
            {"id": "u1", "raw_start": 0, "raw_end": 8, "evidence": RAW[0:8]},
            {"id": "u1", "raw_start": 9, "raw_end": 20, "evidence": RAW[9:20]},
        ]
    }
    report = validate_semantic_analysis(RAW, payload)
    assert report.rejections[0].violation is Violation.DUPLICATE_UNIT_ID


def test_reference_to_a_nonexistent_unit_is_rejected() -> None:
    payload = {
        "semantic_units": [{"id": "u1", "raw_start": 0, "raw_end": 8, "evidence": RAW[0:8]}],
        "references": [
            {
                "marker_start": 0,
                "marker_end": 8,
                "marker_evidence": RAW[0:8],
                "status": "resolved",
                "target_unit_id": "does-not-exist",
            }
        ],
    }
    report = validate_semantic_analysis(RAW, payload)
    assert report.rejections[0].violation is Violation.UNKNOWN_UNIT_ID
    assert report.accepted_reference_indexes == ()


def test_referent_outside_the_models_own_candidates_is_rejected() -> None:
    """A choice made outside the options offered is unaccounted for."""

    payload = {
        "semantic_units": [
            {"id": "u1", "raw_start": 0, "raw_end": 8, "evidence": RAW[0:8]},
            {"id": "u2", "raw_start": 9, "raw_end": 20, "evidence": RAW[9:20]},
        ],
        "references": [
            {
                "marker_start": 0,
                "marker_end": 8,
                "marker_evidence": RAW[0:8],
                "status": "resolved",
                "target_unit_id": "u2",
                "candidate_unit_ids": ["u1"],
            }
        ],
    }
    report = validate_semantic_analysis(RAW, payload)
    assert report.rejections[0].violation is Violation.REFERENT_NOT_A_CANDIDATE


def test_resolved_reference_without_a_target_is_rejected() -> None:
    payload = {
        "semantic_units": [{"id": "u1", "raw_start": 0, "raw_end": 8, "evidence": RAW[0:8]}],
        "references": [
            {
                "marker_start": 0,
                "marker_end": 8,
                "marker_evidence": RAW[0:8],
                "status": "resolved",
            }
        ],
    }
    report = validate_semantic_analysis(RAW, payload)
    assert report.rejections[0].violation is Violation.RESOLVED_WITHOUT_TARGET


def test_an_unresolved_reference_is_allowed_to_stay_unresolved() -> None:
    payload = {
        "semantic_units": [{"id": "u1", "raw_start": 0, "raw_end": 8, "evidence": RAW[0:8]}],
        "references": [
            {
                "marker_start": 0,
                "marker_end": 8,
                "marker_evidence": RAW[0:8],
                "status": "unresolved",
            }
        ],
    }
    report = validate_semantic_analysis(RAW, payload)
    assert report.ok
    assert report.accepted_reference_indexes == (0,)


# --------------------------------------------------------------------------
# Fail-closed fallback
# --------------------------------------------------------------------------


def test_no_analysis_at_all_abstains() -> None:
    """No model, no analysis, no candidate -- and no rule-based substitute."""

    assert validate_semantic_analysis(RAW, {}).fallback is SemanticFallback.ABSTAIN


def test_a_wholly_invalid_analysis_retains_raw_only() -> None:
    report = validate_semantic_analysis(RAW, _payload(RAW, evidence="invented"))
    assert report.fallback is SemanticFallback.RETAIN_RAW_ONLY


def test_a_partially_valid_analysis_keeps_the_good_units() -> None:
    payload = {
        "semantic_units": [
            {"id": "u1", "raw_start": 0, "raw_end": 8, "evidence": RAW[0:8]},
            {"id": "u2", "raw_start": 9, "raw_end": 20, "evidence": "invented"},
        ]
    }
    report = validate_semantic_analysis(RAW, payload)
    assert report.accepted_unit_ids == ("u1",)
    assert report.fallback is None


def test_the_envelope_is_the_only_thing_a_model_answer_is_checked_against() -> None:
    """End to end: envelope offsets, model answer, deterministic verdict."""

    envelope: TextEnvelope = build_envelope(FIELD_TRACE)
    sentence = envelope.sentences[2]
    faithful = {
        "semantic_units": [
            {
                "id": "u1",
                "raw_start": sentence.raw_start,
                "raw_end": sentence.raw_end,
                "evidence": FIELD_TRACE[sentence.raw_start : sentence.raw_end],
                "proposition": "Kubuntu's advantage is removing the WSL2 layer.",
            }
        ]
    }
    assert validate_semantic_analysis(FIELD_TRACE, faithful).ok

    shifted = json.loads(json.dumps(faithful))
    shifted["semantic_units"][0]["raw_start"] += 1
    assert not validate_semantic_analysis(FIELD_TRACE, shifted).ok


def test_context_hint_detection_is_a_closed_class() -> None:
    """Ordinary nouns produce no hint; pronouns and demonstratives do."""

    assert detect_context_dependency("کوبونتو یه مزیت دارد.") == ()
    assert detect_context_dependency("Docker runs on Windows.") == ()
    assert detect_context_dependency("درباره اش صحبت کنیم.")
    assert detect_context_dependency("This layer is slow.")
