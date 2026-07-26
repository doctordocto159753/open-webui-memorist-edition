"""PRD-02 WP01: token-boundary corpus for Persian, English, and identifiers.

Each false-positive fixture asserts twice: that the needle really does occur as
a substring (so the fixture is not vacuous) and that token matching rejects it
anyway. A fixture that stopped being a substring would fail loudly rather than
pass for the wrong reason.
"""

from __future__ import annotations

import pytest

from memcore.memory_worker.extraction.sensitivity import classify_sensitivity
from memcore.memory_worker.semantic.factors import ReceiverKind, resolve_receiver
from memcore.models import SensitivityClass
from memcore.textsemantics import contains_token, normalize_text

ZWNJ = "‌"

# Persian verbs and nouns that contain a lexicon token as a bare substring.
# "تیم" (team) sits inside a whole family of first-person plural past verbs.
PERSIAN_FALSE_POSITIVES: list[tuple[str, str]] = [
    ("تیم", "تصمیم گرفتیم از Jira استفاده کنیم."),
    ("تیم", "گرفتیم"),
    ("تیم", "ما دیروز رفتیم."),
    ("تیم", "این را قبلا گفتیم."),
    ("تیم", "راه حل را یافتیم."),
    ("تیم", "پیشنهاد را پذیرفتیم."),
    ("تیم", "این ماژول را ساختیم."),
    ("تیم", "هزینه را پرداختیم."),
    ("تیم", "فایل را انداختیم دور."),
    ("تیم", "لاگ‌ها را ریختیم بیرون."),
    ("تیم", "قرار را گذاشتیم."),
    ("تیم", "قبلا مستندات داشتیم."),
    ("تیم", "تست‌ها را نوشتیم."),
    ("تیم", "ما اینجا هستیم."),
    ("تیم", "ما مسئول این بخش نیستیم."),
    ("تیم", "توانستیم مشکل را حل کنیم."),
    ("تیم", "این را از قبل دانستیم."),
    ("تیم", "قرارداد را بستیم."),
    ("تو", "توسعه محصول را شروع کردیم."),
    ("تو", "تولید نسخه جدید انجام شد."),
    ("تو", "توضیح بیشتری لازم است."),
    ("تو", "این ستون باید حذف شود."),
    ("تو", "دستور را اجرا کن."),
    ("شما", "شمارش رکوردها اشتباه است."),
    ("شما", "شماره تیکت را بفرست."),
    ("شما", "شمال شهر ترافیک دارد."),
    ("مدل", "این متن مدلسازی شده است."),
    ("کلید", "کلیدواژه‌ها را استخراج کن."),
    ("رمز", "رمزگذاری داده‌ها فعال است."),
]

# Real references that must keep matching.
PERSIAN_TRUE_POSITIVES: list[tuple[str, str]] = [
    ("تیم", "تیم"),
    ("تیم", "ما تیم محصول را تشکیل دادیم."),
    ("تیم", f"تیم{ZWNJ}های توسعه آماده‌اند."),
    ("تیم", "این تیم مسئول است."),
    ("تیم", "تیم، لطفا بررسی کنید."),
    ("تیم", "«تیم» باید تصمیم بگیرد."),
    ("تیم", "مسئولیت با تیم است."),
    ("تیم", "تیمِ محصول را خبر کن."),
    ("تو", "تو باید این کار را انجام دهی."),
    ("شما", "شما باید این پرامپت را اصلاح کنید."),
    ("مدل", "این مدل خوب جواب می‌دهد."),
    ("کلید", "کلید API را عوض کن."),
    ("رمز", "رمز را در جای امن نگه دار."),
]

ENGLISH_FALSE_POSITIVES: list[tuple[str, str]] = [
    ("token", "tokenizer settings are wrong"),
    ("token", "tokenization changed"),
    ("secret", "the secretary will confirm"),
    ("password", "passwordless login is enabled"),
    ("team", "steamer basket"),
    ("team", "teamwork matters"),
    ("you", "young engineers"),
    ("you", "bring your laptop"),
    ("ai", "she said so"),
    ("ai", "send an email"),
    ("ai", "maintain the branch"),
    ("use", "because of the outage"),
    ("use", "the house rules"),
    ("add", "the address changed"),
    ("add", "padding is wrong"),
    ("route", "the router rebooted"),
    ("model", "modelling the load"),
    ("prompt", "prompting users twice"),
]

ENGLISH_TRUE_POSITIVES: list[tuple[str, str]] = [
    ("token", "rotate the token"),
    ("token", "rotate the access_token"),
    ("token", "the token, please"),
    ("secret", "store the secret safely"),
    ("password", "the password is wrong"),
    # An underscore is a token boundary, so a snake_case identifier exposes its
    # parts rather than hiding them from a lexical rule.
    ("password", "password_hash is stored"),
    ("team", "the team ships weekly"),
    ("team", "team-level decision"),
    ("you", "you must review it"),
    ("ai", "ask the ai first"),
    ("use", "use Jira for tracking"),
    ("add", "add a status column"),
    ("route", "route it to review"),
    ("model", "the model is slow"),
    ("prompt", "the prompt wording changed"),
]


@pytest.mark.parametrize(("needle", "text"), PERSIAN_FALSE_POSITIVES + ENGLISH_FALSE_POSITIVES)
def test_substring_occurrences_do_not_match_as_tokens(needle: str, text: str) -> None:
    assert needle in normalize_text(text), (
        f"fixture is vacuous: {needle!r} is not a substring of the normalized text"
    )
    assert contains_token(text, needle) is False


@pytest.mark.parametrize(("needle", "text"), PERSIAN_TRUE_POSITIVES + ENGLISH_TRUE_POSITIVES)
def test_real_references_still_match_as_tokens(needle: str, text: str) -> None:
    assert contains_token(text, needle) is True


def test_reported_persian_false_positive_no_longer_reaches_the_receiver() -> None:
    """The exact defect: "تیم" detected inside "گرفتیم"."""

    decided = resolve_receiver("تصمیم گرفتیم از Jira استفاده کنیم.")
    real_team = resolve_receiver("تیم باید از Jira استفاده کند.")

    assert decided.kind == ReceiverKind.UNKNOWN.value
    assert decided.evidence is None
    assert real_team.kind == ReceiverKind.TEAM.value
    assert real_team.evidence == "تیم"


@pytest.mark.parametrize(
    "text",
    [
        "توسعه محصول را شروع کردیم.",
        "شمارش رکوردها اشتباه است.",
        "این متن مدلسازی شده است.",
    ],
)
def test_persian_substrings_no_longer_resolve_an_ai_receiver(text: str) -> None:
    assert resolve_receiver(text).kind == ReceiverKind.UNKNOWN.value


@pytest.mark.parametrize(
    "text",
    [
        "شما باید این پرامپت را اصلاح کنید.",
        "تو باید این کار را انجام دهی.",
        "این مدل خوب جواب می‌دهد.",
    ],
)
def test_real_persian_addressees_still_resolve(text: str) -> None:
    assert resolve_receiver(text).kind == ReceiverKind.AI.value


def test_zwnj_and_plain_space_spellings_agree() -> None:
    """Persian writers use ZWNJ and a space interchangeably."""

    assert resolve_receiver(f"توسعه{ZWNJ}دهنده باید تست بنویسد.").kind == ReceiverKind.TEAM.value
    assert resolve_receiver("توسعه دهنده باید تست بنویسد.").kind == ReceiverKind.TEAM.value


def test_receiver_evidence_is_the_exact_raw_substring() -> None:
    raw = "تیم‌های محصول باید بررسی کنند."
    match = resolve_receiver(raw)

    assert match.evidence is not None
    assert match.evidence in raw, "evidence must be readable back out of the raw message"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("tokenizer settings", SensitivityClass.NORMAL),
        ("task-force meeting", SensitivityClass.NORMAL),
        ("the secretary will confirm", SensitivityClass.NORMAL),
        ("healthy margins", SensitivityClass.NORMAL),
        ("rotate the token", SensitivityClass.SECRET),
        ("rotate the access_token", SensitivityClass.SECRET),
        ("my password is wrong", SensitivityClass.SECRET),
        ("the api key expired", SensitivityClass.SECRET),
        ("the api_key expired", SensitivityClass.SECRET),
        ("key sk-abcdefgh12345", SensitivityClass.SECRET),
        ("medical history", SensitivityClass.SENSITIVE),
        ("پزشکی و سلامت", SensitivityClass.SENSITIVE),
    ],
)
def test_sensitivity_is_token_aware(text: str, expected: SensitivityClass) -> None:
    assert classify_sensitivity(text) is expected


def test_secrets_inside_fenced_code_are_still_detected() -> None:
    """Excluding code from lexical rules must not open a secret-leak hole."""

    raw = "Here is my config:\n```env\nAPI_KEY=abc123\n```\n"
    assert classify_sensitivity(raw) is SensitivityClass.SECRET


def test_sensitivity_ignores_case_and_persian_digits_in_code() -> None:
    raw = "```\nPassword = '۱۲۳۴'\n```"
    assert classify_sensitivity(raw) is SensitivityClass.SECRET
