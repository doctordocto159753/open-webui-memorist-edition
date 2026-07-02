from collections.abc import Sequence

from memcore.memory_worker import reason_codes as reasons
from memcore.memory_worker.contracts import CheapGateClassifier, GateResult
from memcore.models import GateDecisionValue, MemoryGateDecision, TextUnit
from memcore.validators.ijson import dump_ijson

STRONG_SIGNALS = (
    "remember this",
    "don't forget",
    "do not forget",
    "from now on",
    "prefer",
    "i prefer",
    "we decided",
    "we have decided",
    "do not ",
    "don't ",
    "never ",
    "always ",
    "my name is",
    "by ",
    "i mean",
    "از این به بعد",
    "یادت باشد",
    "یادت باشه",
    "من ترجیح می",
    "ترجیح می‌دهم",
    "تصمیم گرفتیم",
    "نباید",
    "همیشه",
    "هرگز",
    "فعلا",
    "فعلاً",
    "نام من",
    "منظورم از",
)
LOW_VALUE = (
    "hi",
    "hello",
    "thanks",
    "thank you",
    "ok",
    "okay",
    "test",
    "ping",
    "سلام",
    "مرسی",
    "ممنون",
    "باشه",
    "تست",
)
SENSITIVE_TERMS = (
    "religion",
    "political",
    "medical",
    "health",
    "password",
    "token",
    "secret",
    "api key",
    "مذهب",
    "سیاسی",
    "پزشکی",
    "رمز",
)


class DeterministicGate:
    def __init__(self, classifier: CheapGateClassifier | None = None) -> None:
        self.classifier = classifier

    def evaluate(self, unit: TextUnit) -> GateResult:
        text = unit.text.strip()
        lowered = text.lower()
        reason_codes: list[str] = []
        strong = _contains_any(lowered, STRONG_SIGNALS)
        low_value = lowered in LOW_VALUE or _is_low_value(lowered)
        sensitive = _contains_any(lowered, SENSITIVE_TERMS)

        if "forget this" in lowered or "forget that" in lowered or "فراموش کن" in lowered:
            reason_codes.append(reasons.FORGET_REQUEST)
            return GateResult(
                decision=GateDecisionValue.MANUAL_REVIEW,
                reason_codes=reason_codes,
                salience_score=0.8,
                persistence_score=0.2,
                actionability_score=0.8,
                sensitivity_score=0.7,
                novelty_score=0.5,
                requires_high_confidence_pass=True,
            )

        if low_value:
            reason_codes.append(reasons.LOW_VALUE_PHATIC)
            if not strong:
                reason_codes.append(reasons.GREETING_ONLY)
                return GateResult(
                    decision=GateDecisionValue.DISCARD,
                    reason_codes=reason_codes,
                    salience_score=0.05,
                    persistence_score=0.0,
                    actionability_score=0.0,
                    sensitivity_score=0.0,
                    novelty_score=0.0,
                )

        if strong:
            reason_codes.extend(_strong_reason_codes(lowered))
        if sensitive:
            reason_codes.append(reasons.SENSITIVE_MENTION)
        if unit.speaker_role == "assistant" and strong:
            reason_codes.append(reasons.ASSISTANT_CLAIM_NOT_USER_FACT)
        if unit.speaker_role == "tool":
            reason_codes.append(reasons.TOOL_OBSERVATION)

        if not reason_codes:
            reason_codes.append(reasons.NO_MEMORY_SIGNAL)

        result = _result_from_reasons(reason_codes, sensitive)
        if self.classifier is None:
            return result

        try:
            model_result = self.classifier.classify(text, reason_codes)
        except Exception:
            result.reason_codes.append(reasons.MODEL_CLASSIFIER_FAILED)
            return result
        return model_result or result

    def to_model(self, unit: TextUnit, processing_run_uuid: str) -> MemoryGateDecision:
        result = self.evaluate(unit)
        return MemoryGateDecision(
            text_unit_uuid=unit.text_unit_uuid,
            processing_run_uuid=processing_run_uuid,
            decision=result.decision,
            reason_codes_ijson=dump_ijson(result.reason_codes),
            salience_score=result.salience_score,
            persistence_score=result.persistence_score,
            actionability_score=result.actionability_score,
            sensitivity_score=result.sensitivity_score,
            novelty_score=result.novelty_score,
            requires_high_confidence_pass=result.requires_high_confidence_pass,
        )


def _contains_any(text: str, needles: Sequence[str]) -> bool:
    return any(needle in text for needle in needles)


def _is_low_value(text: str) -> bool:
    normalized = text.strip(" .!?؟").lower()
    return normalized in LOW_VALUE or len(normalized) <= 2


def _strong_reason_codes(text: str) -> list[str]:
    codes = [reasons.STRONG_MEMORY_SIGNAL]
    if "prefer" in text or "ترجیح" in text:
        codes.append(reasons.EXPLICIT_PREFERENCE)
    if "do not" in text or "don't" in text or "never" in text or "نباید" in text or "هرگز" in text:
        codes.append(reasons.EXPLICIT_CONSTRAINT)
    if "decided" in text or "تصمیم گرفتیم" in text:
        codes.append(reasons.PROJECT_DECISION)
    if "i mean" in text or "منظورم از" in text:
        codes.append(reasons.DEFINITION_SIGNAL)
    if "my name is" in text or "نام من" in text:
        codes.append(reasons.IDENTITY_SIGNAL)
    if "currently" in text or "فعلا" in text or "فعلاً" in text:
        codes.append(reasons.TEMPORARY_STATE)
    return codes


def _result_from_reasons(reason_codes: list[str], sensitive: bool) -> GateResult:
    if reasons.NO_MEMORY_SIGNAL in reason_codes:
        return GateResult(
            decision=GateDecisionValue.RETAIN_RAW_ONLY,
            reason_codes=reason_codes,
            salience_score=0.2,
            persistence_score=0.1,
            actionability_score=0.0,
            sensitivity_score=0.0,
            novelty_score=0.2,
        )
    if sensitive:
        return GateResult(
            decision=GateDecisionValue.MANUAL_REVIEW,
            reason_codes=reason_codes,
            salience_score=0.75,
            persistence_score=0.5,
            actionability_score=0.4,
            sensitivity_score=0.8,
            novelty_score=0.5,
            requires_high_confidence_pass=True,
        )
    if reasons.EXPLICIT_CONSTRAINT in reason_codes or reasons.PROJECT_DECISION in reason_codes:
        return GateResult(
            decision=GateDecisionValue.ANALYZE_HIGH_CONFIDENCE,
            reason_codes=reason_codes,
            salience_score=0.9,
            persistence_score=0.85,
            actionability_score=0.9,
            sensitivity_score=0.1,
            novelty_score=0.7,
            requires_high_confidence_pass=True,
        )
    return GateResult(
        decision=GateDecisionValue.ANALYZE,
        reason_codes=reason_codes,
        salience_score=0.75,
        persistence_score=0.75,
        actionability_score=0.45,
        sensitivity_score=0.05,
        novelty_score=0.6,
    )
