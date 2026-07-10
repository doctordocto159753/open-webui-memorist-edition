from __future__ import annotations

import re
from collections import Counter
from time import perf_counter
from typing import Any, Protocol

from memcore.memory_worker.contracts import JOB_MEMORY_SIGNAL_ROUTING
from memcore.memory_worker.jakobson.mapper import map_output_to_annotations
from memcore.memory_worker.jakobson.validator import validate_jakobson_provider_output
from memcore.memory_worker.prompts.registry import (
    PromptExecutionRepository,
    render_prompt,
    validate_prompt_execution,
)
from memcore.memory_worker.prompts.versions import (
    JAKOBSON_SENTENCE_ANALYSIS_PROMPT_ID,
    JAKOBSON_SENTENCE_ANALYSIS_VERSION,
)
from memcore.memory_worker.providers.openai_compatible import (
    OpenAICompatibleMemoryExtractionProvider,
)
from memcore.memory_worker.routing.signal_router import SignalRouter
from memcore.memory_worker.segmentation.sentence_segmenter import SentenceSegmenter
from memcore.model_control.security import sanitize_error_message
from memcore.models import (
    JakobsonAnalysisRun,
    JakobsonAnalysisRunStatus,
    JakobsonFunction,
    Message,
    TextUnit,
    TextUnitType,
)
from memcore.repositories import (
    JobRepository,
    MessageRepository,
    RepositoryError,
    SessionRepository,
)
from memcore.repositories.memory_worker import (
    JakobsonAnalysisRepository,
    MemorySignalRouteRepository,
    MemoryStoreRepository,
    TextUnitRepository,
)
from memcore.validators.ijson import canonical_hash_ijson, dump_ijson


class JakobsonProvider(Protocol):
    provider_type: str
    model_name: str

    def analyze(self, units: list[TextUnit], raw_text: str) -> dict[str, Any]: ...


class DeterministicJakobsonProvider:
    def __init__(
        self,
        provider_type: str = "deterministic",
        model_name: str = "deterministic-jakobson-v2",
    ) -> None:
        self.provider_type = provider_type
        self.model_name = model_name

    def analyze(self, units: list[TextUnit], raw_text: str) -> dict[str, Any]:
        sentences = [_analyze_unit(unit, index + 1) for index, unit in enumerate(units)]
        function_counts = Counter(sentence["dominant_function"] for sentence in sentences)
        dominant = function_counts.most_common(1)[0][0] if function_counts else "referential"
        secondary = [
            function for function, _ in function_counts.most_common() if function != dominant
        ]
        return {
            "schema_version": "1.0",
            "prompt_id": JAKOBSON_SENTENCE_ANALYSIS_PROMPT_ID,
            "prompt_version": JAKOBSON_SENTENCE_ANALYSIS_VERSION,
            "status": "ok",
            "warnings": [],
            "items": [],
            "analysis_level": "sentence",
            "model": "jakobson_six_factor",
            "input_language": _overall_language(units),
            "sentence_count": len(sentences),
            "sentences": sentences,
            "overall_summary": {
                "dominant_overall_function": dominant,
                "secondary_overall_functions": secondary,
                "main_sender": (
                    "user" if any(unit.speaker_role == "user" for unit in units) else None
                ),
                "main_receiver": _overall_receiver(sentences),
                "main_context": _overall_context(sentences),
                "main_code": _overall_code(sentences),
                "main_contact_channel": "chat",
            },
        }


class OpenAICompatibleJakobsonProvider:
    def __init__(self, profile: dict[str, Any], timeout_ms: int = 8000) -> None:
        self.provider_type = str(
            profile.get("provider_type") or profile.get("provider") or "openai_compatible"
        )
        self.model_name = str(profile.get("model_name") or "unknown")
        self.provider = OpenAICompatibleMemoryExtractionProvider.from_profile(
            profile, timeout_ms=timeout_ms
        )
        self.input_tokens = 0
        self.output_tokens = 0
        self.latency_ms = 0

    def analyze(self, units: list[TextUnit], raw_text: str) -> dict[str, Any]:
        input_value = _jakobson_input(units)
        prompt = render_prompt(
            JAKOBSON_SENTENCE_ANALYSIS_PROMPT_ID,
            JAKOBSON_SENTENCE_ANALYSIS_VERSION,
            input_value,
        )
        response = self.provider.extract(system_prompt=prompt, input_payload=input_value)
        validate_prompt_execution(
            JAKOBSON_SENTENCE_ANALYSIS_PROMPT_ID,
            JAKOBSON_SENTENCE_ANALYSIS_VERSION,
            input_value,
            response.output,
        )
        self.input_tokens = response.input_tokens
        self.output_tokens = response.output_tokens
        self.latency_ms = response.latency_ms
        return response.output


class JakobsonAnalysisService:
    def __init__(
        self,
        connection: Any,
        provider: JakobsonProvider | None = None,
        segmenter: SentenceSegmenter | None = None,
    ) -> None:
        self.connection = connection
        self.provider: JakobsonProvider = (
            provider if provider is not None else DeterministicJakobsonProvider()
        )
        self.segmenter = segmenter or SentenceSegmenter()
        self.messages = MessageRepository(connection)
        self.sessions = SessionRepository(connection)
        self.units = TextUnitRepository(connection)
        self.runs = JakobsonAnalysisRepository(connection)
        self.routes = MemorySignalRouteRepository(connection)
        self.jobs = JobRepository(connection)
        self.outbox = MemoryStoreRepository(connection)
        self.router = SignalRouter()
        self.model_profile_uuid: str | None = None
        self.model_role = "memory_extraction"

    def run_for_message(
        self,
        message_uuid: str,
        import_run_uuid: str | None = None,
        job_uuid: str | None = None,
    ) -> dict[str, Any]:
        message = self.messages.get_message(message_uuid)
        if message is None:
            raise RepositoryError(f"Message not found: {message_uuid}")
        raw_text = message.raw_text or ""
        units = self._load_or_create_sentence_units(message, raw_text)
        input_value = _jakobson_input(units)
        session = self.sessions.get_session(message.session_uuid)
        output: dict[str, Any] | None = None
        run = JakobsonAnalysisRun(
            workspace_uuid=session.workspace_uuid if session else None,
            project_uuid=session.project_uuid if session else None,
            session_uuid=message.session_uuid,
            message_uuid=message.message_uuid,
            prompt_id=JAKOBSON_SENTENCE_ANALYSIS_PROMPT_ID,
            prompt_version=JAKOBSON_SENTENCE_ANALYSIS_VERSION,
            provider_type=self.provider.provider_type,
            model_name=self.provider.model_name,
            input_hash=canonical_hash_ijson(input_value),
            status=JakobsonAnalysisRunStatus.SUCCEEDED,
            warnings_ijson=dump_ijson([]),
        )
        started = perf_counter()
        try:
            output = self.provider.analyze(units, raw_text)
            validate_jakobson_provider_output(output)
            run.output_hash = canonical_hash_ijson(output)
            annotations, warnings = map_output_to_annotations(
                analysis_run_uuid=run.analysis_run_uuid,
                message_uuid=message.message_uuid,
                units=units,
                output=output,
            )
            run.warnings_ijson = dump_ijson(warnings)
            run.raw_output_ijson = dump_ijson(output)
            execution = PromptExecutionRepository(self.connection).record_execution(
                prompt_id=JAKOBSON_SENTENCE_ANALYSIS_PROMPT_ID,
                prompt_version=JAKOBSON_SENTENCE_ANALYSIS_VERSION,
                model_role=self.model_role,
                provider_type=self.provider.provider_type,
                model_name=str(self.provider.model_name or self.provider.provider_type),
                model_profile_uuid=self.model_profile_uuid,
                workspace_uuid=session.workspace_uuid if session else None,
                project_uuid=session.project_uuid if session else None,
                session_uuid=message.session_uuid,
                message_uuid=message.message_uuid,
                import_run_uuid=import_run_uuid,
                job_uuid=job_uuid,
                input_value=input_value,
                input_ref=f"message:{message.message_uuid}",
                raw_output_value=output,
                validated_output_value=output,
                status="ok",
                warnings=warnings,
                latency_ms=int(
                    getattr(self.provider, "latency_ms", 0) or (perf_counter() - started) * 1000
                ),
                input_tokens=int(
                    getattr(self.provider, "input_tokens", 0) or max(0, (len(raw_text) + 3) // 4)
                ),
                output_tokens=int(getattr(self.provider, "output_tokens", 0) or len(annotations)),
            )
            run.prompt_execution_uuid = str(execution["prompt_execution_uuid"])
            persisted_run = self.runs.create_run(run)
            persisted_annotations = self.runs.create_annotations(annotations)
            routes = []
            for annotation in persisted_annotations:
                routes.extend(self.routes.create_routes(self.router.route(annotation)))
            self.jobs.enqueue_job_once(
                JOB_MEMORY_SIGNAL_ROUTING,
                {
                    "message_uuid": message.message_uuid,
                    "analysis_run_uuid": persisted_run.analysis_run_uuid,
                    "annotation_uuids": [
                        annotation.annotation_uuid for annotation in persisted_annotations
                    ],
                },
                priority=65,
            )
            self.outbox.enqueue_projection(
                "jakobson_analysis_run",
                persisted_run.analysis_run_uuid,
                "jakobson_annotations_ready",
                {
                    "message_uuid": message.message_uuid,
                    "analysis_run_uuid": persisted_run.analysis_run_uuid,
                    "annotation_uuids": [
                        annotation.annotation_uuid for annotation in persisted_annotations
                    ],
                },
            )
            return {
                "analysis_run_uuid": persisted_run.analysis_run_uuid,
                "message_uuid": message.message_uuid,
                "sentence_units": len(units),
                "annotations": len(persisted_annotations),
                "routes": len(routes),
                "warnings": warnings,
            }
        except Exception as error:
            run.status = JakobsonAnalysisRunStatus.FAILED
            run.warnings_ijson = dump_ijson([str(error)])
            if output is not None:
                run.raw_output_ijson = dump_ijson(output)
                run.output_hash = canonical_hash_ijson(output)
            try:
                execution = PromptExecutionRepository(self.connection).record_execution(
                    prompt_id=JAKOBSON_SENTENCE_ANALYSIS_PROMPT_ID,
                    prompt_version=JAKOBSON_SENTENCE_ANALYSIS_VERSION,
                    model_role=self.model_role,
                    provider_type=self.provider.provider_type,
                    model_name=str(self.provider.model_name or self.provider.provider_type),
                    model_profile_uuid=self.model_profile_uuid,
                    workspace_uuid=session.workspace_uuid if session else None,
                    project_uuid=session.project_uuid if session else None,
                    session_uuid=message.session_uuid,
                    message_uuid=message.message_uuid,
                    import_run_uuid=import_run_uuid,
                    job_uuid=job_uuid,
                    input_value=input_value,
                    input_ref=f"message:{message.message_uuid}",
                    raw_output_value=output,
                    validated_output_value=None,
                    status="error",
                    warnings=[type(error).__name__],
                    error_sanitized=sanitize_error_message(str(error)),
                    latency_ms=int(
                        getattr(self.provider, "latency_ms", 0) or (perf_counter() - started) * 1000
                    ),
                    input_tokens=int(
                        getattr(self.provider, "input_tokens", 0)
                        or max(0, (len(raw_text) + 3) // 4)
                    ),
                )
                run.prompt_execution_uuid = str(execution["prompt_execution_uuid"])
            except Exception:
                pass
            self.runs.create_run(run)
            raise

    def _load_or_create_sentence_units(self, message: Message, raw_text: str) -> list[TextUnit]:
        existing = self.units.list_units(message.message_uuid)
        sentence_units = [unit for unit in existing if unit.unit_type is TextUnitType.SENTENCE]
        if sentence_units and len(sentence_units) == len(existing):
            return sentence_units
        if existing:
            raise RepositoryError(
                "message already has non-sentence text units; re-unitize before Jakobson analysis"
            )
        return self.units.create_units(
            self.segmenter.to_text_units(
                message_uuid=message.message_uuid,
                session_uuid=message.session_uuid,
                speaker_role=message.role.value,
                text=raw_text,
            )
        )


def _jakobson_input(units: list[TextUnit]) -> dict[str, Any]:
    message_uuid = units[0].message_uuid if units else None
    return {
        "message_uuid": message_uuid,
        "prompt_id": JAKOBSON_SENTENCE_ANALYSIS_PROMPT_ID,
        "prompt_version": JAKOBSON_SENTENCE_ANALYSIS_VERSION,
        "sentences": [
            {
                "id": index + 1,
                "unit_uuid": unit.text_unit_uuid,
                "text": unit.text,
                "char_start": unit.start_char,
                "char_end": unit.end_char,
            }
            for index, unit in enumerate(units)
        ],
    }


def _analyze_unit(unit: TextUnit, sentence_id: int) -> dict[str, Any]:
    text = unit.text
    dominant, secondary, reason = _classify_function(text)
    receiver = _receiver(text, dominant)
    context = _context(text)
    code = _code(text, unit.language_hint or unit.language_code)
    return {
        "id": sentence_id,
        "text": text,
        "six_factors": {
            "sender_addresser": _factor(unit.speaker_role or "user", text[:80], "high"),
            "receiver_addressee": _factor(receiver, _receiver_evidence(text), "medium"),
            "message": _factor(_compact(text), text[:120], "high"),
            "context_referent": _factor(context, _context_evidence(text), "medium"),
            "code": _factor(code, _code_evidence(text), "high"),
            "contact_channel": _factor("chat", "captured chat message", "medium"),
        },
        "dominant_function": dominant.value,
        "secondary_functions": [item.value for item in secondary],
        "function_reason": reason,
        "notes": "",
    }


def _classify_function(text: str) -> tuple[JakobsonFunction, list[JakobsonFunction], str]:
    lowered = text.lower()
    if _is_phatic(text, lowered):
        return JakobsonFunction.PHATIC, [], "The sentence opens or maintains contact."
    has_instruction = _has_instruction(text, lowered)
    has_metalanguage = _has_metalanguage(text, lowered)
    if has_instruction:
        secondary = [JakobsonFunction.METALINGUAL] if has_metalanguage else []
        return (
            JakobsonFunction.CONATIVE,
            secondary,
            "The sentence tells the receiver what to do.",
        )
    if has_metalanguage:
        return (
            JakobsonFunction.METALINGUAL,
            [],
            "The sentence defines, translates, or comments on wording.",
        )
    if _has_emotive(text, lowered):
        return JakobsonFunction.EMOTIVE, [], "The sentence expresses sender stance or desire."
    if _has_poetic(text, lowered):
        return JakobsonFunction.POETIC, [], "The sentence foregrounds wording or style."
    return JakobsonFunction.REFERENTIAL, [], "The sentence mainly describes context or facts."


def _has_instruction(text: str, lowered: str) -> bool:
    return bool(
        re.search(r"\b(must|should|do not|don't|add|define|create|implement|use|route)\b", lowered)
        or re.search(r"باید|حتما|تعریف کن|اضافه کن|پیاده‌سازی کن|استفاده کن|نکن|وظیفه", text)
    )


def _has_metalanguage(text: str, lowered: str) -> bool:
    return bool(
        re.search(r"\b(term|terminology|wording|definition|translation|meaning|prompt)\b", lowered)
        or re.search(r"اصطلاح|واژه|تعریف|ترجمه|معنی|منظور|پرامپت", text)
    )


def _has_emotive(text: str, lowered: str) -> bool:
    return bool(
        re.search(r"\b(prefer|want|like|dislike|frustrated|wish|approve)\b", lowered)
        or re.search(r"ترجیح|می‌خواهم|دوست دارم|کلافه|ناراحت|نگران|راضی", text)
    )


def _has_poetic(text: str, lowered: str) -> bool:
    return bool(re.search(r"\b(slogan|rhythm|wordplay|branding tone)\b", lowered) or "شعار" in text)


def _is_phatic(text: str, lowered: str) -> bool:
    stripped = text.strip(" .!؟?")
    return stripped in {"سلام", "درود", "hello", "hi", "hey"} or lowered.strip() in {
        "hello",
        "hi",
        "hey",
        "thanks",
    }


def _receiver(text: str, dominant: JakobsonFunction) -> str | None:
    lowered = text.lower()
    if re.search(r"product team|team|developer|تیم|توسعه‌دهنده|برنامه‌نویس", text, re.I):
        return "Product Team"
    if re.search(r"\b(ai|assistant|model|you)\b|هوش مصنوعی|دستیار|مدل|تو|شما", lowered):
        return "AI"
    if dominant is JakobsonFunction.CONATIVE:
        return "AI"
    return None


def _context(text: str) -> str | None:
    lowered = text.lower()
    if "jira" in lowered or "جیرا" in text:
        return "Jira workflow"
    if re.search(r"process|workflow|pipeline|project|فرایند|فرآیند|ورک‌فلو|پروژه", lowered):
        return "project workflow"
    if "prompt" in lowered or "پرامپت" in text:
        return "prompt policy"
    return _compact(text)[:80]


def _code(text: str, language_hint: str | None) -> str:
    language = {
        "fa": "Persian",
        "en": "English",
        "mixed": "Persian/English",
    }.get(language_hint or "", "Unknown language")
    if "jira" in text.lower():
        return f"{language}; Jira/product workflow terminology"
    if "api" in text.lower() or "FastAPI" in text:
        return f"{language}; technical API terminology"
    if "پرامپت" in text or "prompt" in text.lower():
        return f"{language}; prompt-engineering terminology"
    return f"{language}; chat register"


def _factor(value: str | None, evidence: str | None, confidence: str) -> dict[str, str | None]:
    return {"value": value, "evidence": evidence, "confidence": confidence}


def _compact(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _receiver_evidence(text: str) -> str | None:
    match = re.search(
        r"product team|team|developer|AI|assistant|تیم|هوش مصنوعی|دستیار|تو|شما",
        text,
        re.I,
    )
    return match.group(0) if match else None


def _context_evidence(text: str) -> str | None:
    match = re.search(r"Jira|workflow|process|project|جیرا|فرایند|فرآیند|ورک‌فلو|پروژه", text, re.I)
    return match.group(0) if match else text[:80]


def _code_evidence(text: str) -> str | None:
    match = re.search(r"Jira|API|FastAPI|prompt|پرامپت|اصطلاح", text, re.I)
    return match.group(0) if match else text[:40]


def _overall_language(units: list[TextUnit]) -> str:
    languages = {unit.language_hint or unit.language_code or "unknown" for unit in units}
    if "mixed" in languages or ({"fa", "en"} <= languages):
        return "mixed"
    return next(iter(languages), "unknown")


def _overall_receiver(sentences: list[dict[str, Any]]) -> str | None:
    values = [
        sentence["six_factors"]["receiver_addressee"]["value"]
        for sentence in sentences
        if sentence["six_factors"]["receiver_addressee"]["value"]
    ]
    return Counter(values).most_common(1)[0][0] if values else None


def _overall_context(sentences: list[dict[str, Any]]) -> str | None:
    values = [
        sentence["six_factors"]["context_referent"]["value"]
        for sentence in sentences
        if sentence["six_factors"]["context_referent"]["value"]
    ]
    return Counter(values).most_common(1)[0][0] if values else None


def _overall_code(sentences: list[dict[str, Any]]) -> str | None:
    values = [
        sentence["six_factors"]["code"]["value"]
        for sentence in sentences
        if sentence["six_factors"]["code"]["value"]
    ]
    return Counter(values).most_common(1)[0][0] if values else None
