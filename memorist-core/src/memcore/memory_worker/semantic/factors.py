from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from memcore.models import JakobsonFunction
from memcore.textsemantics import (
    Lexicon,
    NormalizedText,
    normalize_with_mapping,
)

SEMANTIC_LEXICON_VERSION = "prd02-wp01-semantic-lexicon-v1"


class ReceiverKind(StrEnum):
    AI = "ai"
    TEAM = "team"
    UNKNOWN = "unknown"


class ContextKind(StrEnum):
    JIRA = "jira"
    PROCESS = "process"
    METALINGUAL = "metalingual"
    EMOTIVE = "emotive"
    POETIC = "poetic"
    RESOURCE = "resource"
    PRIVACY = "privacy"
    NONE = "none"


@dataclass(frozen=True)
class SemanticFactorMatch:
    kind: str
    value: str | None
    evidence: str | None
    confidence: str = "medium"


@dataclass(frozen=True)
class ResolvedSemanticFactors:
    receiver: SemanticFactorMatch
    context: SemanticFactorMatch


# These lexicons are matched on token boundaries, never as substrings, so
# "تیم" no longer fires inside "گرفتیم" and "token" no longer fires inside
# "tokenizer". Inflected forms the previous substring patterns happened to
# catch are listed explicitly rather than recovered by stemming.
AI_RECEIVER = Lexicon(
    name="ai_receiver",
    phrases=(
        "ai",
        "assistant",
        "model",
        "chatgpt",
        "you",
        "هوش مصنوعی",
        "دستیار",
        "مدل",
        "تو",
        "شما",
    ),
)
TEAM_RECEIVER = Lexicon(
    name="team_receiver",
    phrases=(
        "product team",
        "team",
        "teams",
        "developer",
        "developers",
        "squad",
        "squads",
        "تیم",
        "توسعه‌دهنده",
        "برنامه‌نویس",
        "اسکواد",
    ),
)
HIGH_PRIORITY_INSTRUCTION = Lexicon(
    name="high_priority_instruction",
    phrases=(
        "must",
        "should",
        "have to",
        "do not",
        "don't",
        "add",
        "define",
        "create",
        "implement",
        "route",
        "use",
        "باید",
        "حتما",
        "وظیفه",
        "تعریف کن",
        "اضافه کن",
        "پیاده‌سازی کن",
        "استفاده کن",
        "نکن",
        "نکنید",
        "نکنیم",
    ),
)
JIRA_CONTEXT = Lexicon(name="jira_context", phrases=("jira", "جیرا"))
PROCESS_CONTEXT = Lexicon(
    name="process_context",
    phrases=(
        "process",
        "workflow",
        "project",
        "product",
        "pipeline",
        "system logic",
        "configuration",
        "فرایند",
        "فرآیند",
        "ورک‌فلو",
        "جریان کار",
        "پروژه",
        "محصول",
        "محصولات",
        "منطق سیستم",
        "کانفیگ",
    ),
)
METALINGUAL_CONTEXT = Lexicon(
    name="metalingual_context",
    phrases=(
        "term",
        "terminology",
        "wording",
        "definition",
        "translation",
        "meaning",
        "i mean",
        "mean",
        "means",
        "defined as",
        "prompt wording",
        "prompt",
        "اصطلاح",
        "واژه",
        "تعریف",
        "ترجمه",
        "معنی",
        "منظور",
        "عبارت",
        "متن پرامپت",
        "پرامپت",
    ),
)
EMOTIVE_CONTEXT = Lexicon(
    name="emotive_context",
    phrases=(
        "prefer",
        "want",
        "like",
        "dislike",
        "frustrated",
        "annoyed",
        "approve",
        "wish",
        "ترجیح",
        "می‌خواهم",
        "دوست دارم",
        "ناراحتم",
        "کلافه",
        "راضی",
        "نگران",
    ),
)
POETIC_CONTEXT = Lexicon(
    name="poetic_context",
    phrases=("slogan", "style", "rhythm", "tone", "branding", "شعار", "سبک", "لحن", "برند"),
)
PRIVACY_CONTEXT = Lexicon(
    name="privacy_context",
    phrases=(
        "secret",
        "password",
        "token",
        "api key",
        "privacy",
        "رمز",
        "توکن",
        "کلید",
        "حریم خصوصی",
    ),
)

# FORGET_DIRECTIVE and RESOURCE_CONTEXT stay regexes on purpose. They encode
# structure and ordering (an imperative at the start of a sentence, a URL or
# path shape) rather than a word list, so token matching would not express
# them. Neither one is the substring-boundary defect this package fixes.
FORGET_DIRECTIVE = re.compile(
    r"^\s*(?:please\s+)?(?:forget|delete|erase|remove)\b|"
    r"\b(?:please|can you|could you|would you|want you to|need you to)\s+"
    r"(?:forget|delete|erase|remove)\b|"
    r"\b(?:delete|erase|remove)\b.{0,80}\b(?:memory|memories|data|records?)\b|"
    r"فراموش کن|حذف کن|پاک کن|حافظه.{0,40}(?:حذف|پاک)",
    re.I,
)
RESOURCE_CONTEXT = re.compile(r"https?://|www\.|file:|مسیر فایل|لینک|منبع", re.I)


def resolve_semantic_factors(
    text: str,
    *,
    dominant_function: JakobsonFunction | None = None,
    receiver_hint: str | None = None,
    context_hint: str | None = None,
) -> ResolvedSemanticFactors:
    """Resolve receiver and context using the shared PR4-D semantic lexicon.

    This is deterministic and side-effect free. Lite and Full both call it, so
    neither runtime can drift into a second lexical authority.
    """

    return ResolvedSemanticFactors(
        receiver=resolve_receiver(
            text,
            dominant_function=dominant_function,
            receiver_hint=receiver_hint,
        ),
        context=resolve_context(text, context_hint=context_hint),
    )


def resolve_receiver(
    text: str,
    *,
    dominant_function: JakobsonFunction | None = None,
    receiver_hint: str | None = None,
) -> SemanticFactorMatch:
    sources = _sources(receiver_hint, text)
    for lexicon, kind, value in (
        (TEAM_RECEIVER, ReceiverKind.TEAM, "Product Team"),
        (AI_RECEIVER, ReceiverKind.AI, "AI"),
    ):
        match = _first_match(lexicon, sources)
        if match is not None:
            return SemanticFactorMatch(
                kind=kind.value,
                value=value,
                evidence=match,
                confidence="high",
            )
    if dominant_function is JakobsonFunction.CONATIVE:
        return SemanticFactorMatch(
            kind=ReceiverKind.AI.value,
            value="AI",
            evidence=None,
            confidence="medium",
        )
    return SemanticFactorMatch(kind=ReceiverKind.UNKNOWN.value, value=None, evidence=None)


def resolve_context(text: str, *, context_hint: str | None = None) -> SemanticFactorMatch:
    sources = _sources(context_hint, text)
    for kind, value, lexicon in (
        (ContextKind.PRIVACY, "privacy-sensitive material", PRIVACY_CONTEXT),
        (ContextKind.JIRA, "Jira workflow", JIRA_CONTEXT),
        (ContextKind.PROCESS, "project workflow", PROCESS_CONTEXT),
        (ContextKind.METALINGUAL, "prompt policy", METALINGUAL_CONTEXT),
    ):
        match = _first_match(lexicon, sources)
        if match is not None:
            return SemanticFactorMatch(
                kind=kind.value,
                value=value,
                evidence=match,
                confidence="high",
            )
    resource = _first_regex_match(RESOURCE_CONTEXT, sources)
    if resource is not None:
        return SemanticFactorMatch(
            kind=ContextKind.RESOURCE.value,
            value="resource reference",
            evidence=resource,
            confidence="high",
        )
    for kind, value, lexicon in (
        (ContextKind.POETIC, "style or branding", POETIC_CONTEXT),
        (ContextKind.EMOTIVE, "sender stance or preference", EMOTIVE_CONTEXT),
    ):
        match = _first_match(lexicon, sources)
        if match is not None:
            return SemanticFactorMatch(
                kind=kind.value,
                value=value,
                evidence=match,
                confidence="high",
            )
    compact = _compact(text)
    return SemanticFactorMatch(
        kind=ContextKind.NONE.value,
        value=compact[:80] or None,
        evidence=compact[:80] or None,
        confidence="medium",
    )


def _sources(hint: str | None, text: str) -> tuple[NormalizedText, ...]:
    """Normalize the hint and the text separately, hint first.

    Keeping them apart is what makes the recorded evidence exact: a hit in the
    hint reports the hint's own raw substring instead of an offset into a
    concatenation that exists nowhere in the stored message.
    """

    return tuple(normalize_with_mapping(value) for value in (hint, text) if value)


def _first_match(lexicon: Lexicon, sources: tuple[NormalizedText, ...]) -> str | None:
    for source in sources:
        match = lexicon.search(source)
        if match is not None:
            return match.evidence
    return None


def _first_regex_match(pattern: re.Pattern[str], sources: tuple[NormalizedText, ...]) -> str | None:
    for source in sources:
        match = pattern.search(source.raw)
        if match is not None:
            return match.group(0)
    return None


def _compact(text: str) -> str:
    """Whitespace-compact a snippet for display.

    This is presentation only. It is deliberately not the shared normalization
    contract: the fallback context value should read back as the user wrote it.
    """

    return re.sub(r"\s+", " ", text).strip()
