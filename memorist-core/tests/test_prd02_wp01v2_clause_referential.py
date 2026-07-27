"""PRD-02 WP01 v2: clause spans, referential signals, and the field-trace delta.

WP01's job is not to decide what a memory should say. It is to make the text
structured enough that the package which *does* decide cannot be forced into
guessing. These tests pin that boundary from both sides: the structure is
present and exact, and the structure stops short of asserting a referent.
"""

from __future__ import annotations

import json
import random
import unicodedata

import pytest

from memcore.textsemantics import (
    MAX_ANTECEDENT_CANDIDATES,
    TEXT_SEMANTICS_CONTRACT_VERSION,
    ClauseKind,
    MarkerType,
    Polarity,
    ReferentialConfidence,
    ResolutionScope,
    analyze_text,
    identifier_phrases,
    normalize_with_mapping,
    normalized_span_for_raw_span,
    raw_span_for_normalized_span,
    segment_clauses,
    segment_sentences,
)

# The sanitized field fixture. Every line is what a real user wrote during the
# traced conversation; nothing identifying it survives here, and the misspelled
# "Kubunto" is kept on purpose -- see the spell-correction test below.
FIELD_TRACE = (
    "میخوام از ویندوز مهاجرت کنم به سیستم عامل Kubunto یا اوبونتو عادی.\n"
    "مهم ترین مزایایی که الان از لینوکس به نظرم اومد جذابن این هست که "
    "سرعت و عملکرد و تناسب با برنامه نویسی دارد.\n"
    "کوبونتو یه مزیت دارد: حذف لایه WSL2.\n"
    "الان احتمالاً Docker را روی WSL2 اجرا می‌کنیم در ویندوز.\n"
    "میخوام بیشتر درباره این مزیت بدونم بعدا.\n"
    "الان فقط خیلی کوتاه بهم توضیح بده و یادت باشه بعدا درباره اش صحبت کنیم.\n"
)

SHORT_FIELD = (
    "کوبونتو یه مزیت دارد: حذف لایه WSL2.\n"
    "الان احتمالاً Docker را روی WSL2 اجرا می‌کنیم در ویندوز.\n"
    "میخوام بیشتر درباره این مزیت بدونم بعدا.\n"
    "الان فقط خیلی کوتاه بهم توضیح بده و یادت باشه بعدا درباره اش صحبت کنیم.\n"
)


def _clause_texts(text: str) -> list[str]:
    return [clause.text for clause in analyze_text(text).clauses]


def _marker_for(text: str, needle: str):  # type: ignore[no-untyped-def]
    result = analyze_text(text)
    return [
        marker
        for marker in result.referential_markers
        if needle in text[marker.raw_start : marker.raw_end]
    ]


# --------------------------------------------------------------------------
# Clause segmentation
# --------------------------------------------------------------------------


def test_simple_persian_sentence_is_one_clause() -> None:
    assert _clause_texts("من از ویندوز استفاده می‌کنم.") == ["من از ویندوز استفاده می‌کنم."]


def test_colon_splits_the_explanation_into_its_own_clause() -> None:
    clauses = analyze_text("کوبونتو یه مزیت دارد: حذف لایه WSL2.").clauses
    assert [clause.text for clause in clauses] == ["کوبونتو یه مزیت دارد:", "حذف لایه WSL2."]
    assert clauses[1].kind is ClauseKind.EXPLANATION
    assert clauses[1].boundary_reason == "colon_explanation"


def test_compound_sentence_splits_at_a_verb_final_conjunction() -> None:
    clauses = analyze_text("الان فقط خیلی کوتاه بهم توضیح بده و یادت باشه بعدا صحبت کنیم.").clauses
    assert len(clauses) == 2
    assert clauses[0].text == "الان فقط خیلی کوتاه بهم توضیح بده"
    assert clauses[1].boundary_reason == "coordinating_conjunction_after_verb"


def test_noun_coordination_is_never_split_and_says_so() -> None:
    """`سرعت و عملکرد و تناسب` is one noun phrase, not three propositions.

    Splitting here would fabricate claims the writer never made, so the rule
    declines and records a warning instead of inventing certainty.
    """

    text = "سرعت و عملکرد و تناسب با برنامه نویسی دارد."
    result = analyze_text(text)
    assert len(result.clauses) == 1
    assert "coordinating_conjunction_not_split" in result.warnings


def test_comma_before_conjunction_is_a_safe_split() -> None:
    """A comma is enough on its own, with no clause-final verb in front of it."""

    clauses = analyze_text("We use Jira, and the team likes it.").clauses
    assert len(clauses) == 2
    assert clauses[1].boundary_reason == "coordinating_conjunction_after_comma"


def test_contrastive_connective_opens_a_clause() -> None:
    clauses = analyze_text("Docker خوب است اما WSL2 کند است.").clauses
    assert len(clauses) == 2
    assert clauses[1].boundary_reason == "contrastive_connective"
    assert clauses[1].text.startswith("اما")


def test_question_clause_is_classified_as_a_question() -> None:
    clauses = analyze_text("چرا WSL2 کند است؟").clauses
    assert clauses[-1].kind is ClauseKind.QUESTION


def test_imperative_clause_is_classified_as_an_instruction() -> None:
    assert analyze_text("لطفا خیلی کوتاه توضیح بده.").clauses[0].kind is ClauseKind.INSTRUCTION
    assert analyze_text("Please explain briefly.").clauses[0].kind is ClauseKind.INSTRUCTION


def test_multiline_text_produces_one_sentence_per_line() -> None:
    sentences = analyze_text(SHORT_FIELD).sentences
    assert len(sentences) == 4
    assert all(not sentence.is_code for sentence in sentences)


def test_punctuation_free_informal_persian_stays_one_clause_with_a_warning() -> None:
    result = analyze_text("میخوام بیشتر درباره لینوکس بدونم")
    assert len(result.clauses) == 1
    assert "sentence_without_terminator" in result.warnings


def test_decimal_and_dotted_identifiers_do_not_end_a_sentence() -> None:
    for text in ("We use GPT-5.4 today.", "See example.com now.", "Version 1.2.3 shipped."):
        assert len(analyze_text(text).sentences) == 1, text


def test_abbreviation_does_not_end_a_sentence() -> None:
    assert len(analyze_text("Use Docker, e.g. on WSL2, for now.").sentences) == 1


def test_fenced_code_is_its_own_clause_and_is_never_prose() -> None:
    text = "این تنظیمات است:\n```\nKEY=ABC و تیم\n```\nتمام شد."
    result = analyze_text(text)
    code = [clause for clause in result.clauses if clause.kind is ClauseKind.CODE]
    assert len(code) == 1
    assert "KEY=ABC" in code[0].text
    assert text[code[0].raw_start : code[0].raw_end] == code[0].text
    # Prose either side of the fence still segments normally.
    assert any(clause.text == "تمام شد." for clause in result.clauses)


def test_blank_line_is_a_hard_sentence_boundary() -> None:
    assert len(analyze_text("اولی بدون نقطه\n\nدومی بدون نقطه").sentences) == 2


# --------------------------------------------------------------------------
# Span integrity
# --------------------------------------------------------------------------


@pytest.mark.parametrize("text", [FIELD_TRACE, SHORT_FIELD, "Docker on WSL2.\nتیم آماده است."])
def test_every_span_reconstructs_its_exact_raw_evidence(text: str) -> None:
    result = analyze_text(text)
    for sentence in result.sentences:
        assert text[sentence.raw_start : sentence.raw_end] == sentence.text
    for clause in result.clauses:
        assert text[clause.raw_start : clause.raw_end] == clause.text
    for marker in result.referential_markers:
        assert marker.raw_end > marker.raw_start
        assert text[marker.raw_start : marker.raw_end].strip()
    for candidate in (
        candidate
        for marker in result.referential_markers
        for candidate in marker.antecedent_candidate_spans
    ):
        assert text[candidate.raw_start : candidate.raw_end] == candidate.text


def test_clauses_never_overlap_and_stay_inside_their_sentence() -> None:
    result = analyze_text(FIELD_TRACE)
    by_sentence: dict[int, list[tuple[int, int]]] = {}
    for clause in result.clauses:
        sentence = result.sentences[clause.sentence_index]
        assert sentence.raw_start <= clause.raw_start
        assert clause.raw_end <= sentence.raw_end
        by_sentence.setdefault(clause.sentence_index, []).append((clause.raw_start, clause.raw_end))
    for spans in by_sentence.values():
        ordered = sorted(spans)
        for earlier, later in zip(ordered, ordered[1:], strict=False):
            assert earlier[1] <= later[0], "clause spans must not overlap"


def test_reverse_span_mapping_round_trips() -> None:
    text = "الان احتمالاً Docker را روی WSL2 اجرا می‌کنیم."
    mapped = normalize_with_mapping(text)
    for start in range(len(mapped.text)):
        raw_start, raw_end = raw_span_for_normalized_span(mapped, start, start + 1)
        back = normalized_span_for_raw_span(mapped, raw_start, raw_end)
        assert back is not None
        assert back[0] <= start < back[1]


def test_reverse_span_mapping_returns_none_for_unrepresented_raw_text() -> None:
    """Dropped characters have no normalized position, and that is a real answer."""

    text = "سلام‏‏"  # trailing right-to-left marks are dropped
    mapped = normalize_with_mapping(text)
    assert normalized_span_for_raw_span(mapped, len(text) - 2, len(text)) is None


def test_reverse_span_mapping_rejects_impossible_ranges() -> None:
    mapped = normalize_with_mapping("سلام")
    with pytest.raises(ValueError):
        normalized_span_for_raw_span(mapped, 3, 3)
    with pytest.raises(ValueError):
        normalized_span_for_raw_span(mapped, 0, 999)


def test_span_mapping_property_over_generated_mixed_script_text() -> None:
    """Offsets stay exact across randomly assembled Persian/Latin/ZWNJ text."""

    alphabet = ["ی", "ي", "ک", "ك", "ا", "‌", " ", "\n", "a", "Z", "2", ".", "،", "ً", "-"]
    generator = random.Random(20260727)
    for _ in range(400):
        raw = "".join(generator.choice(alphabet) for _ in range(generator.randint(1, 60)))
        mapped = normalize_with_mapping(raw)
        assert mapped.raw == raw
        for index in range(len(mapped.text)):
            raw_start, raw_end = mapped.raw_span(index, index + 1)
            assert 0 <= raw_start < raw_end <= len(raw)
        result = analyze_text(raw)
        for clause in result.clauses:
            assert raw[clause.raw_start : clause.raw_end] == clause.text


# --------------------------------------------------------------------------
# Identifier phrases
# --------------------------------------------------------------------------


def test_identifiers_split_by_tokenization_are_recoverable_as_phrases() -> None:
    text = "set MEMORIST_MEMORY_EXTRACTION_API_KEY and use GPT-5.4 with api_key."
    phrases = {match.evidence for match in identifier_phrases(text)}
    assert "MEMORIST_MEMORY_EXTRACTION_API_KEY" in phrases
    assert "GPT-5.4" in phrases
    assert "api_key" in phrases


def test_single_token_identifiers_are_left_intact_and_not_repeated() -> None:
    result = analyze_text("WSL2 and PostgreSQL and Docker are fine.")
    assert {"wsl2", "postgresql", "docker"} <= {token.key for token in result.tokens}
    assert result.phrases == ()


def test_contractions_are_not_treated_as_identifiers() -> None:
    assert identifier_phrases("we don't deploy on Friday") == ()


# --------------------------------------------------------------------------
# Referential markers
# --------------------------------------------------------------------------


def test_demonstrative_phrase_is_marked_and_carries_the_noun() -> None:
    markers = _marker_for(SHORT_FIELD, "این مزیت")
    assert len(markers) == 1
    marker = markers[0]
    assert marker.marker_type is MarkerType.DEMONSTRATIVE_PHRASE
    assert SHORT_FIELD[marker.raw_start : marker.raw_end] == "این مزیت"
    assert marker.requires_context is True


def test_bound_pronoun_reference_is_marked_and_covers_the_clitic() -> None:
    markers = _marker_for(SHORT_FIELD, "درباره اش")
    assert len(markers) == 1
    marker = markers[0]
    assert marker.requires_context is True
    assert "اش" in SHORT_FIELD[marker.raw_start : marker.raw_end]


def test_local_antecedent_span_is_offered_without_being_chosen() -> None:
    """`حذف لایه WSL2` is *a* candidate for `این مزیت`, never *the* answer."""

    marker = _marker_for(SHORT_FIELD, "این مزیت")[0]
    candidates = marker.antecedent_candidate_spans
    assert "حذف لایه WSL2." in [candidate.text for candidate in candidates]
    assert len(candidates) > 1, "a single candidate would read as a resolution"
    assert marker.resolution_scope_hint is ResolutionScope.PRIOR_CLAUSE
    assert "unresolved_by_contract" in marker.reason_codes
    assert "multiple_antecedent_candidates" in marker.reason_codes


def test_no_field_on_the_contract_can_express_a_chosen_referent() -> None:
    """The absence of a resolution field is the guarantee, so it is asserted."""

    marker = _marker_for(SHORT_FIELD, "این مزیت")[0]
    forbidden = {"resolved_antecedent", "referent", "resolution", "antecedent"}
    assert forbidden.isdisjoint(vars(marker))
    payload = json.loads(analyze_text(SHORT_FIELD).as_json())
    assert forbidden.isdisjoint(payload["referential_markers"][0])


def test_instruction_clauses_are_never_offered_as_antecedents() -> None:
    marker = _marker_for(SHORT_FIELD, "درباره اش")[0]
    result = analyze_text(SHORT_FIELD)
    instruction_ids = {
        clause.clause_id for clause in result.clauses if clause.kind is ClauseKind.INSTRUCTION
    }
    offered = {candidate.clause_id for candidate in marker.antecedent_candidate_spans}
    assert offered.isdisjoint(instruction_ids)


def test_antecedent_candidates_are_nearest_first_and_bounded() -> None:
    marker = _marker_for(FIELD_TRACE, "درباره اش")[0]
    distances = [candidate.distance_clauses for candidate in marker.antecedent_candidate_spans]
    assert distances == sorted(distances)
    assert len(distances) <= MAX_ANTECEDENT_CANDIDATES


def test_marker_with_no_preceding_subject_matter_points_outside_the_message() -> None:
    marker = _marker_for("درباره اش صحبت کنیم.", "درباره اش")[0]
    assert marker.antecedent_candidate_spans == ()
    assert marker.resolution_scope_hint is ResolutionScope.PRIOR_MESSAGE
    assert "no_local_antecedent" in marker.reason_codes


def test_ordinary_nouns_carry_no_marker() -> None:
    for text in (
        "کوبونتو یه مزیت دارد.",
        "Docker runs on Windows.",
        "سرعت و عملکرد خوب است.",
    ):
        assert analyze_text(text).referential_markers == (), text


def test_code_identifiers_are_not_treated_as_pronouns() -> None:
    result = analyze_text("```\nthis = that\nconst it = 1\n```")
    assert result.referential_markers == ()


def test_english_demonstratives_in_mixed_text_are_marked() -> None:
    result = analyze_text("ما Docker داریم. This layer is slow.")
    markers = [
        marker
        for marker in result.referential_markers
        if "This" in marker.text or "this" in marker.text
    ]
    assert markers
    assert markers[0].requires_context is True


def test_ambiguous_english_markers_are_labelled_ambiguous() -> None:
    result = analyze_text("Docker is fine. It is slow.")
    markers = [marker for marker in result.referential_markers if marker.text.strip() == "it"]
    assert markers
    assert markers[0].confidence_label is ReferentialConfidence.MARKER_AMBIGUOUS


def test_demonstrative_before_a_verb_stays_a_bare_demonstrative() -> None:
    """`این هست` is a determiner plus a verb, not a noun phrase."""

    markers = _marker_for("جذاب این هست که سرعت دارد.", "این")
    assert markers
    assert markers[0].marker_type is MarkerType.DEMONSTRATIVE


def test_markers_never_overlap_each_other() -> None:
    markers = analyze_text(FIELD_TRACE).referential_markers
    spans = sorted((marker.raw_start, marker.raw_end) for marker in markers)
    for earlier, later in zip(spans, spans[1:], strict=False):
        assert earlier[1] <= later[0]


# --------------------------------------------------------------------------
# Clause-level polarity
# --------------------------------------------------------------------------


def test_polarity_is_reported_per_clause_not_per_message() -> None:
    """One negated clause must not drag its neighbour negative."""

    result = analyze_text("من از ویندوز استفاده نمی‌کنم و لینوکس را دوست دارم.")
    cues = {cue.clause_id: cue.polarity for cue in result.polarity_cues}
    assert len(cues) == len(result.clauses)
    assert Polarity.NEGATED in cues.values()


def test_polarity_cue_evidence_points_at_real_raw_text() -> None:
    text = "ما هرگز روز جمعه دیپلوی نمی‌کنیم."
    result = analyze_text(text)
    negated = [cue for cue in result.polarity_cues if cue.polarity is Polarity.NEGATED]
    assert negated
    for cue in negated:
        assert cue.raw_start is not None and cue.raw_end is not None
        assert text[cue.raw_start : cue.raw_end] == cue.evidence


def test_uncertainty_is_not_read_as_negation() -> None:
    """`احتمالاً` is epistemic hedging; the claim is still asserted."""

    result = analyze_text("الان احتمالاً Docker را روی WSL2 اجرا می‌کنیم در ویندوز.")
    assert [cue.polarity for cue in result.polarity_cues] == [Polarity.AFFIRMED]


def test_code_clauses_have_unknown_polarity() -> None:
    result = analyze_text("```\nnot a claim\n```")
    assert all(cue.polarity is Polarity.UNKNOWN for cue in result.polarity_cues)


# --------------------------------------------------------------------------
# Contract shape and determinism
# --------------------------------------------------------------------------


def test_result_is_versioned_and_json_serializable() -> None:
    result = analyze_text(FIELD_TRACE)
    assert result.contract_version == TEXT_SEMANTICS_CONTRACT_VERSION
    payload = json.loads(result.as_json())
    assert payload["contract_version"] == TEXT_SEMANTICS_CONTRACT_VERSION
    assert payload["raw_text_hash"] == result.raw_text_hash
    assert json.dumps(payload)  # nothing exotic survived into the payload


def test_audit_payload_carries_no_raw_text() -> None:
    """Offsets and codes are safe to copy around; the text itself is not."""

    secret = "my key is sk-ant-api03-abcdefghijklmnop"
    payload = analyze_text(f"{SHORT_FIELD}\n{secret}").as_json()
    assert "sk-ant-api03" not in payload
    assert "کوبونتو" not in payload


def test_replay_of_the_same_text_is_byte_identical() -> None:
    assert analyze_text(FIELD_TRACE).as_json() == analyze_text(FIELD_TRACE).as_json()


def test_result_is_immutable_at_the_api_boundary() -> None:
    result = analyze_text(SHORT_FIELD)
    with pytest.raises(AttributeError):
        result.contract_version = "tampered"  # type: ignore[misc]
    with pytest.raises(AttributeError):
        result.clauses[0].kind = ClauseKind.CODE  # type: ignore[misc]


def test_empty_and_whitespace_text_produce_an_empty_but_valid_result() -> None:
    for text in ("", "   ", "\n\n"):
        result = analyze_text(text)
        assert result.clauses == ()
        assert result.referential_markers == ()
        assert json.loads(result.as_json())["contract_version"]


def test_decomposed_input_still_yields_exact_clause_spans() -> None:
    text = unicodedata.normalize("NFD", "کافه خوب است. رزومه آماده است.")
    result = analyze_text(text)
    assert len(result.sentences) == 2
    for clause in result.clauses:
        assert text[clause.raw_start : clause.raw_end] == clause.text


def test_segment_helpers_agree_with_the_assembled_result() -> None:
    """The pieces and the whole cannot drift apart."""

    sentences, _ = segment_sentences(FIELD_TRACE)
    clauses, _ = segment_clauses(FIELD_TRACE, sentences)
    result = analyze_text(FIELD_TRACE)
    assert [clause.text for clause in clauses] == [clause.text for clause in result.clauses]
