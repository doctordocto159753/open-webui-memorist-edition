from typing import Any, Protocol

from memcore.memory_worker.analysis.modality import modality_payload
from memcore.memory_worker.analysis.schemas import AnalysisRequest, AnalysisResponse
from memcore.memory_worker.analysis.temporal import resolve_temporal_expressions

JAKOBSON_KEYS = (
    "referential",
    "emotive",
    "conative",
    "phatic",
    "metalingual",
    "poetic",
)


class StructuredAnalysisProvider(Protocol):
    def analyze(
        self,
        request: AnalysisRequest,
        response_schema: dict[str, Any],
    ) -> AnalysisResponse: ...


class DeterministicStructuredAnalysisProvider:
    def analyze(
        self,
        request: AnalysisRequest,
        response_schema: dict[str, Any],
    ) -> AnalysisResponse:
        text = request.text.strip()
        lowered = text.lower()
        speech_acts = _speech_acts(lowered)
        source_span = {
            "start": request.text.find(text),
            "end": request.text.find(text) + len(text),
            "text": text,
        }
        return AnalysisResponse(
            speech_acts=speech_acts,
            jakobson_functions=_jakobson_scores(lowered, speech_acts),
            conceptual_nuclei=[
                {
                    "nucleus_type": speech_acts[0] if speech_acts else "assertion",
                    "label": _label_for(lowered),
                    "participants": [],
                    "topic": _label_for(lowered),
                    "proposition": text,
                    "source_span": source_span,
                    "confidence": 0.75,
                }
            ]
            if text
            else [],
            entity_mentions=[],
            temporal_expressions=resolve_temporal_expressions(text, request.source_timestamp),
            modality=modality_payload(text),
            memory_signals=_memory_signals(lowered),
            abstention={"abstained": False, "reason": None},
        )


def _speech_acts(text: str) -> list[str]:
    if not text:
        return []
    acts: list[str] = []
    if "?" in text or "؟" in text:
        acts.append("question")
    if "prefer" in text or "ترجیح" in text:
        acts.append("preference")
    if "decided" in text or "تصمیم گرفتیم" in text:
        acts.append("decision")
    if "do not" in text or "don't" in text or "نباید" in text:
        acts.append("command")
    if "i mean" in text or "منظورم از" in text:
        acts.append("definition")
    if text in {"hi", "hello", "سلام"}:
        acts.append("greeting")
    if not acts:
        acts.append("assertion")
    return acts


def _jakobson_scores(text: str, speech_acts: list[str]) -> dict[str, float]:
    scores = {key: 0.0 for key in JAKOBSON_KEYS}
    scores["referential"] = 0.7
    if "preference" in speech_acts:
        scores["emotive"] = 0.6
    if "command" in speech_acts:
        scores["conative"] = 0.8
    if "greeting" in speech_acts:
        scores["phatic"] = 0.9
        scores["referential"] = 0.1
    if "definition" in speech_acts:
        scores["metalingual"] = 0.9
    if "!" in text:
        scores["emotive"] = max(scores["emotive"], 0.5)
    return scores


def _memory_signals(text: str) -> list[str]:
    signals: list[str] = []
    if "prefer" in text or "ترجیح" in text:
        signals.append("preference")
    if "decided" in text or "تصمیم گرفتیم" in text:
        signals.append("decision")
    if "do not" in text or "don't" in text or "نباید" in text:
        signals.append("constraint")
    if "i mean" in text or "منظورم از" in text:
        signals.append("definition")
    return signals


def _label_for(text: str) -> str:
    for marker in ("prefer", "decided", "do not", "don't", "i mean"):
        if marker in text:
            return marker.replace(" ", "_")
    if "ترجیح" in text:
        return "preference"
    if "تصمیم گرفتیم" in text:
        return "decision"
    return "assertion"
