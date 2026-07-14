from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from memcore.models import JakobsonFunction


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


AI_RECEIVER = re.compile(
    r"\b(ai|assistant|model|chatgpt|you)\b|هوش مصنوعی|دستیار|مدل|تو|شما",
    re.I,
)
TEAM_RECEIVER = re.compile(
    r"product team|team|developer|squad|تیم|توسعه‌دهنده|برنامه‌نویس|اسکواد",
    re.I,
)
HIGH_PRIORITY_INSTRUCTION = re.compile(
    r"\b(must|should|have to|do not|don't|add|define|create|implement|route|use)\b|"
    r"باید|حتما|وظیفه|تعریف کن|اضافه کن|پیاده‌سازی کن|استفاده کن|نکن",
    re.I,
)
JIRA_CONTEXT = re.compile(r"\bjira\b|جیرا", re.I)
PROCESS_CONTEXT = re.compile(
    r"\b(process|workflow|project|product|pipeline|system logic|configuration)\b|"
    r"فرایند|فرآیند|ورک‌فلو|جریان کار|پروژه|محصول|منطق سیستم|کانفیگ",
    re.I,
)
METALINGUAL_CONTEXT = re.compile(
    r"\b(term|terminology|wording|definition|translation|meaning|prompt wording|prompt)\b|"
    r"اصطلاح|واژه|تعریف|ترجمه|معنی|منظور|عبارت|متن پرامپت|پرامپت",
    re.I,
)
EMOTIVE_CONTEXT = re.compile(
    r"\b(prefer|want|like|dislike|frustrated|annoyed|approve|wish)\b|"
    r"ترجیح|می‌خواهم|دوست دارم|ناراحتم|کلافه|راضی|نگران",
    re.I,
)
POETIC_CONTEXT = re.compile(
    r"\b(slogan|style|rhythm|tone|branding)\b|شعار|سبک|لحن|برند",
    re.I,
)
RESOURCE_CONTEXT = re.compile(r"https?://|www\.|file:|مسیر فایل|لینک|منبع", re.I)
PRIVACY_CONTEXT = re.compile(
    r"\b(secret|password|token|api key|privacy)\b|رمز|توکن|کلید|حریم خصوصی",
    re.I,
)


def resolve_semantic_factors(
    text: str,
    *,
    dominant_function: JakobsonFunction | None = None,
    receiver_hint: str | None = None,
    context_hint: str | None = None,
) -> ResolvedSemanticFactors:
    """Resolve receiver and context using the shared PR4-D semantic lexicon.

    This is intentionally deterministic and side-effect free. Task 03 wires the
    existing Jakobson deterministic path and signal routing to this resolver;
    later tasks can expand the resolver without creating more regex forks.
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
    haystack = " ".join(value for value in (receiver_hint, text) if value)
    team_match = TEAM_RECEIVER.search(haystack)
    if team_match:
        return SemanticFactorMatch(
            kind=ReceiverKind.TEAM.value,
            value="Product Team",
            evidence=team_match.group(0),
            confidence="high",
        )
    ai_match = AI_RECEIVER.search(haystack)
    if ai_match:
        return SemanticFactorMatch(
            kind=ReceiverKind.AI.value,
            value="AI",
            evidence=ai_match.group(0),
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
    haystack = " ".join(value for value in (context_hint, text) if value)
    for kind, value, pattern in (
        (ContextKind.PRIVACY, "privacy-sensitive material", PRIVACY_CONTEXT),
        (ContextKind.JIRA, "Jira workflow", JIRA_CONTEXT),
        (ContextKind.PROCESS, "project workflow", PROCESS_CONTEXT),
        (ContextKind.METALINGUAL, "prompt policy", METALINGUAL_CONTEXT),
        (ContextKind.RESOURCE, "resource reference", RESOURCE_CONTEXT),
        (ContextKind.POETIC, "style or branding", POETIC_CONTEXT),
        (ContextKind.EMOTIVE, "sender stance or preference", EMOTIVE_CONTEXT),
    ):
        match = pattern.search(haystack)
        if match:
            return SemanticFactorMatch(
                kind=kind.value,
                value=value,
                evidence=match.group(0),
                confidence="high",
            )
    compact = _compact(text)
    return SemanticFactorMatch(
        kind=ContextKind.NONE.value,
        value=compact[:80] or None,
        evidence=compact[:80] or None,
        confidence="medium",
    )


def _compact(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()
