"""Single source of truth for machine-readable prompt output contracts.

One typed model per prompt id/version drives, without drift:

* the strict provider ``response_format`` JSON Schema;
* runtime validation (with path/code diagnostics for bounded repair);
* the canonical valid example embedded in the system prompt and docs;
* the contract hash used by certification and audit.

Historical prompt versions keep their original validators (see
``memcore.memory_worker.prompts.validators``); this module owns the *new*
canonical Jakobson v3 contract and any future contract-first prompt versions.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from memcore.memory_worker.prompts.versions import (
    JAKOBSON_SENTENCE_ANALYSIS_PROMPT_ID,
    JAKOBSON_SENTENCE_ANALYSIS_V3_VERSION,
    SEMANTIC_CANDIDATE_ANALYSIS_PROMPT_ID,
    SEMANTIC_CANDIDATE_ANALYSIS_V1_VERSION,
    SEMANTIC_CANDIDATE_ANALYSIS_VERSION,
)

JakobsonFunctionName = Literal[
    "referential",
    "emotive",
    "conative",
    "phatic",
    "metalingual",
    "poetic",
]
Confidence = Literal["high", "medium", "low"]
PromptStatus = Literal["ok", "abstain", "reject", "error"]


class _Strict(BaseModel):
    """Base model that forbids extra keys so JSON Schema stays strict."""

    model_config = ConfigDict(extra="forbid")


class _SemanticStrict(BaseModel):
    """WP02 model output is closed and rejects type coercion."""

    model_config = ConfigDict(extra="forbid", strict=True)


class JakobsonFactor(_Strict):
    value: str | None
    evidence: str | None
    confidence: Confidence


class JakobsonSixFactors(_Strict):
    sender_addresser: JakobsonFactor
    receiver_addressee: JakobsonFactor
    message: JakobsonFactor
    context_referent: JakobsonFactor
    code: JakobsonFactor
    contact_channel: JakobsonFactor


class JakobsonItem(_Strict):
    id: Annotated[int, Field(ge=1)]
    text: Annotated[str, Field(min_length=1)]
    six_factors: JakobsonSixFactors
    dominant_function: JakobsonFunctionName
    secondary_functions: list[JakobsonFunctionName]
    function_reason: str
    notes: str | None


class JakobsonOverallSummary(_Strict):
    dominant_overall_function: JakobsonFunctionName
    secondary_overall_functions: list[JakobsonFunctionName]
    main_sender: str | None
    main_receiver: str | None
    main_context: str | None
    main_code: str | None
    main_contact_channel: str | None


class JakobsonV3Output(_Strict):
    """Canonical Jakobson sentence-analysis output (prompt version 3.0).

    ``items`` is the single canonical collection; the redundant ``sentences``
    array from version 2.0 is intentionally removed. Each item is a complete
    sentence annotation.
    """

    schema_version: Literal["1.0"]
    prompt_id: Literal["memorist.jakobson_sentence_analysis"]
    prompt_version: Literal["3.0"]
    status: PromptStatus
    warnings: list[str]
    items: list[JakobsonItem]
    analysis_level: Literal["sentence"]
    model: Literal["jakobson_six_factor"]
    input_language: str
    sentence_count: int
    overall_summary: JakobsonOverallSummary

    @model_validator(mode="after")
    def _sentence_count_matches(self) -> JakobsonV3Output:
        if self.sentence_count != len(self.items):
            raise ValueError("sentence_count must match items length")
        return self


SemanticUnitType = Literal[
    "statement",
    "instruction",
    "question",
    "explanation",
    "heading",
    "list_item",
    "table_row",
    "code_block",
    "fragment",
]
SemanticDurability = Literal["durable", "transient", "context_only", "unknown"]
SemanticPolarity = Literal["affirmed", "negated", "unknown"]
SemanticEpistemicStatus = Literal["asserted", "hedged", "hypothetical", "questioned", "unknown"]
SemanticReferenceStatus = Literal["resolved", "unresolved", "ambiguous"]
SemanticRelationType = Literal["ratifies", "corrects", "supersedes", "contradicts", "elaborates"]
MessageCategory = Literal[
    "Instruction",
    "Need",
    "Preference",
    "Decision",
    "ProjectArtifact",
    "ProjectState",
    "FactClaim",
    "Hypothesis",
    "Constraint",
    "Question",
    "LearningGoal",
    "Feedback",
    "Correction",
    "Retraction",
    "Process",
    "Other",
]
MessageEpistemicStatus = Literal[
    "asserted",
    "proposed",
    "hypothetical",
    "hedged",
    "questioned",
    "unvalidated",
    "confirmed",
    "corrected",
    "contradicted",
    "retracted",
    "unknown",
]
MemoryKind = Literal[
    "Instruction",
    "Need",
    "Preference",
    "Decision",
    "ProjectArtifact",
    "ProjectState",
    "FactClaim",
    "Hypothesis",
    "Constraint",
    "Question",
    "LearningGoal",
    "Feedback",
    "Correction",
    "Retraction",
]


class MessageCategoryTag(_SemanticStrict):
    category: MessageCategory
    normalized_label: str = ""
    confidence: Annotated[float, Field(ge=0, le=1)] = 0.5


class MessageConceptTag(_SemanticStrict):
    canonical_label: Annotated[str, Field(min_length=1)]
    aliases: list[str] = []
    confidence: Annotated[float, Field(ge=0, le=1)] = 0.5
    raw_start: Annotated[int, Field(ge=0)] | None = None
    raw_end: Annotated[int, Field(gt=0)] | None = None

    @model_validator(mode="after")
    def _span_is_complete(self) -> MessageConceptTag:
        if (self.raw_start is None) != (self.raw_end is None):
            raise ValueError("concept span must be complete or omitted")
        if (
            self.raw_start is not None
            and self.raw_end is not None
            and self.raw_start >= self.raw_end
        ):
            raise ValueError("concept raw_start must be less than raw_end")
        return self


class MessageEntity(_SemanticStrict):
    canonical_name: Annotated[str, Field(min_length=1)]
    entity_type: str = "Entity"
    aliases: list[str] = []
    confidence: Annotated[float, Field(ge=0, le=1)] = 0.5
    raw_start: Annotated[int, Field(ge=0)] | None = None
    raw_end: Annotated[int, Field(gt=0)] | None = None


class MessageProcessReference(_SemanticStrict):
    process_label: Annotated[str, Field(min_length=1)]
    aliases: list[str] = []
    stage_label: str | None = None
    stage_ordinal: Annotated[int, Field(ge=1)] | None = None
    confidence: Annotated[float, Field(ge=0, le=1)] = 0.5
    raw_start: Annotated[int, Field(ge=0)] | None = None
    raw_end: Annotated[int, Field(gt=0)] | None = None


class SemanticUnit(_SemanticStrict):
    id: Annotated[str, Field(min_length=1)]
    raw_start: Annotated[int, Field(ge=0)]
    raw_end: Annotated[int, Field(gt=0)]
    evidence: Annotated[str, Field(min_length=1)]
    proposition: Annotated[str, Field(min_length=1)]
    unit_type: SemanticUnitType
    durability: SemanticDurability
    polarity: SemanticPolarity
    epistemic_status: SemanticEpistemicStatus
    memory_kind: MemoryKind | None = None
    lifecycle_status: str = "unknown"


class SemanticReference(_SemanticStrict):
    id: Annotated[str, Field(min_length=1)]
    source_unit_id: Annotated[str, Field(min_length=1)]
    marker_start: Annotated[int, Field(ge=0)]
    marker_end: Annotated[int, Field(gt=0)]
    marker_evidence: Annotated[str, Field(min_length=1)]
    status: SemanticReferenceStatus
    candidate_referent_ids: list[str]
    selected_referent_id: str | None


class SemanticRelation(_SemanticStrict):
    id: Annotated[str, Field(min_length=1)]
    relation_type: SemanticRelationType
    source_unit_id: Annotated[str, Field(min_length=1)]
    target_referent_id: Annotated[str, Field(min_length=1)]
    evidence_start: Annotated[int, Field(ge=0)]
    evidence_end: Annotated[int, Field(gt=0)]
    evidence: Annotated[str, Field(min_length=1)]


class SemanticAnalysisV1Output(_SemanticStrict):
    """Frozen model-returned WP02 semantic-analysis contract."""

    schema_version: Literal["1.0"]
    prompt_id: Literal["memorist.semantic_candidate_analysis"]
    prompt_version: Literal["1.1"]
    status: Literal["ok", "abstain"]
    warnings: list[str]
    intent: str = "unknown"
    primary_topic: str = "unknown"
    secondary_topic: str = "unknown"
    one_line_summary: str = "unknown > unknown > unknown"
    message_categories: list[MessageCategoryTag] = []
    concept_tags: list[MessageConceptTag] = []
    entities: list[MessageEntity] = []
    process_references: list[MessageProcessReference] = []
    epistemic_status: MessageEpistemicStatus = "unknown"
    temporal_status: str = "unknown"
    importance: Annotated[float, Field(ge=0, le=1)] = 0.5
    explicit_memory_request: bool = False
    semantic_units: list[SemanticUnit]
    references: list[SemanticReference]
    relations: list[SemanticRelation]

    @model_validator(mode="after")
    def _message_metadata_is_bounded(self) -> SemanticAnalysisV1Output:
        if len(self.concept_tags) > 5:
            raise ValueError("concept_tags must contain at most five items")
        if self.one_line_summary.count(" > ") != 2:
            raise ValueError("one_line_summary must use intent > primary topic > secondary topic")
        return self


class LegacySemanticUnitV1(_SemanticStrict):
    id: Annotated[str, Field(min_length=1)]
    raw_start: Annotated[int, Field(ge=0)]
    raw_end: Annotated[int, Field(gt=0)]
    evidence: Annotated[str, Field(min_length=1)]
    proposition: Annotated[str, Field(min_length=1)]
    unit_type: Literal["statement", "instruction", "question", "explanation"]
    durability: SemanticDurability
    polarity: SemanticPolarity
    epistemic_status: SemanticEpistemicStatus


class LegacySemanticAnalysisV1Output(_SemanticStrict):
    """Immutable historical schema for prompt version 1.0."""

    schema_version: Literal["1.0"]
    prompt_id: Literal["memorist.semantic_candidate_analysis"]
    prompt_version: Literal["1.0"]
    status: Literal["ok", "abstain"]
    warnings: list[str]
    semantic_units: list[LegacySemanticUnitV1]
    references: list[SemanticReference]
    relations: list[SemanticRelation]


@dataclass(frozen=True)
class PromptContract:
    """A resolved contract binding a prompt id/version to its typed model."""

    prompt_id: str
    prompt_version: str
    json_schema_name: str
    model: type[BaseModel]

    def json_schema(self) -> dict[str, Any]:
        """Return a strict, provider-compatible JSON Schema for this contract."""

        return _strict_json_schema(self.model)

    @property
    def contract_hash(self) -> str:
        encoded = json.dumps(
            self.json_schema(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def validate(self, output: Any) -> list[dict[str, str]]:
        """Return a list of {path, code, message} issues (empty when valid).

        Never raises for schema problems; callers use the issues to drive
        bounded repair and sanitized audit. The messages are model-structural
        (field names, types) and contain no provider content values.
        """

        try:
            self.model.model_validate(output)
        except ValidationError as error:
            return _issues_from_validation_error(error)
        return []

    def is_valid(self, output: Mapping[str, Any]) -> bool:
        return not self.validate(output)


def _strict_json_schema(model: type[BaseModel]) -> dict[str, Any]:
    schema = model.model_json_schema()
    _make_strict(schema)
    return schema


def _make_strict(node: Any) -> None:
    """Force additionalProperties:false and required-all on every object.

    OpenAI strict ``json_schema`` requires closed objects whose ``required``
    lists every declared property. Nullable fields already carry ``anyOf``/null
    from the typed model, so requiring them does not reject valid null values.
    """

    if isinstance(node, dict):
        if node.get("type") == "object" or "properties" in node:
            properties = node.get("properties")
            if isinstance(properties, dict):
                node["additionalProperties"] = False
                node["required"] = list(properties.keys())
        for value in node.values():
            _make_strict(value)
    elif isinstance(node, list):
        for item in node:
            _make_strict(item)


def _issues_from_validation_error(error: ValidationError) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    for item in error.errors(include_url=False):
        location = ".".join(str(part) for part in item.get("loc", ()))
        issues.append(
            {
                "path": location or "(root)",
                "code": str(item.get("type") or "invalid"),
                "message": str(item.get("msg") or "invalid value"),
            }
        )
    return issues


JAKOBSON_V3_CONTRACT = PromptContract(
    prompt_id=JAKOBSON_SENTENCE_ANALYSIS_PROMPT_ID,
    prompt_version=JAKOBSON_SENTENCE_ANALYSIS_V3_VERSION,
    json_schema_name="memorist_jakobson_sentence_analysis_v3",
    model=JakobsonV3Output,
)

SEMANTIC_CANDIDATE_V1_CONTRACT = PromptContract(
    prompt_id=SEMANTIC_CANDIDATE_ANALYSIS_PROMPT_ID,
    prompt_version=SEMANTIC_CANDIDATE_ANALYSIS_VERSION,
    json_schema_name="memorist_semantic_candidate_analysis_v1",
    model=SemanticAnalysisV1Output,
)

SEMANTIC_CANDIDATE_LEGACY_V1_CONTRACT = PromptContract(
    prompt_id=SEMANTIC_CANDIDATE_ANALYSIS_PROMPT_ID,
    prompt_version=SEMANTIC_CANDIDATE_ANALYSIS_V1_VERSION,
    json_schema_name="memorist_semantic_candidate_analysis_v1_legacy",
    model=LegacySemanticAnalysisV1Output,
)

_CONTRACTS: dict[tuple[str, str], PromptContract] = {
    (JAKOBSON_V3_CONTRACT.prompt_id, JAKOBSON_V3_CONTRACT.prompt_version): JAKOBSON_V3_CONTRACT,
    (
        SEMANTIC_CANDIDATE_V1_CONTRACT.prompt_id,
        SEMANTIC_CANDIDATE_V1_CONTRACT.prompt_version,
    ): SEMANTIC_CANDIDATE_V1_CONTRACT,
    (
        SEMANTIC_CANDIDATE_LEGACY_V1_CONTRACT.prompt_id,
        SEMANTIC_CANDIDATE_LEGACY_V1_CONTRACT.prompt_version,
    ): SEMANTIC_CANDIDATE_LEGACY_V1_CONTRACT,
}


def get_contract(prompt_id: str, prompt_version: str) -> PromptContract | None:
    """Return the typed contract for a prompt id/version, or None if legacy."""

    return _CONTRACTS.get((prompt_id, prompt_version))


def canonical_sentence_items(output: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return sentence annotations from a Jakobson output, dispatched by version.

    Version 3.0 stores them under the canonical ``items`` collection; historical
    2.0 rows store them under ``sentences``. The per-item annotation shape is
    identical across versions, so downstream mapping is version-agnostic once the
    collection is resolved here. An old row is never reinterpreted under the new
    schema: the field is chosen from the row's own recorded prompt version.
    """

    version = str(output.get("prompt_version") or "")
    field = "items" if version == JAKOBSON_SENTENCE_ANALYSIS_V3_VERSION else "sentences"
    collection = output.get(field)
    if not isinstance(collection, list):
        return []
    return [item for item in collection if isinstance(item, dict)]


def canonical_jakobson_v3_example() -> dict[str, Any]:
    """A complete, schema-valid v3 example used by prompts, docs, and tests."""

    def factor(value: str, evidence: str, confidence: Confidence) -> dict[str, Any]:
        return {"value": value, "evidence": evidence, "confidence": confidence}

    return {
        "schema_version": "1.0",
        "prompt_id": JAKOBSON_SENTENCE_ANALYSIS_PROMPT_ID,
        "prompt_version": JAKOBSON_SENTENCE_ANALYSIS_V3_VERSION,
        "status": "ok",
        "warnings": [],
        "items": [
            {
                "id": 1,
                "text": "نوشیدنی مورد علاقهٔ من چیست؟",
                "six_factors": {
                    "sender_addresser": factor("user", "من", "medium"),
                    "receiver_addressee": factor(
                        "assistant", "question addressed in chat", "medium"
                    ),
                    "message": factor(
                        "The user asks for their favorite drink.",
                        "نوشیدنی مورد علاقهٔ من چیست؟",
                        "high",
                    ),
                    "context_referent": factor(
                        "the user's stored drink preference", "نوشیدنی مورد علاقه", "high"
                    ),
                    "code": factor("Persian", "Persian sentence", "high"),
                    "contact_channel": factor("chat", "message input", "high"),
                },
                "dominant_function": "conative",
                "secondary_functions": ["referential"],
                "function_reason": "The sentence asks the receiver to provide information.",
                "notes": "",
            }
        ],
        "analysis_level": "sentence",
        "model": "jakobson_six_factor",
        "input_language": "fa",
        "sentence_count": 1,
        "overall_summary": {
            "dominant_overall_function": "conative",
            "secondary_overall_functions": ["referential"],
            "main_sender": "user",
            "main_receiver": "assistant",
            "main_context": "stored personal preference",
            "main_code": "Persian",
            "main_contact_channel": "chat",
        },
    }


def canonical_semantic_candidate_v1_example() -> dict[str, Any]:
    """Return an example by round-tripping the frozen typed contract."""

    output = SemanticAnalysisV1Output(
        schema_version="1.0",
        prompt_id="memorist.semantic_candidate_analysis",
        prompt_version="1.1",
        status="ok",
        warnings=[],
        intent="instruction",
        primary_topic="backup policy",
        secondary_topic="enabled state",
        one_line_summary="instruction > backup policy > enabled state",
        message_categories=[
            MessageCategoryTag(
                category="Instruction", normalized_label="backup policy", confidence=0.9
            )
        ],
        concept_tags=[
            MessageConceptTag(
                canonical_label="backup policy",
                aliases=["backups"],
                confidence=0.9,
                raw_start=5,
                raw_end=12,
            )
        ],
        entities=[],
        process_references=[],
        epistemic_status="asserted",
        temporal_status="current",
        importance=0.8,
        explicit_memory_request=False,
        semantic_units=[
            SemanticUnit(
                id="unit-1",
                raw_start=0,
                raw_end=21,
                evidence="Keep backups enabled.",
                proposition="Backups must remain enabled.",
                unit_type="instruction",
                durability="durable",
                polarity="affirmed",
                epistemic_status="asserted",
                memory_kind="Instruction",
                lifecycle_status="active",
            )
        ],
        references=[],
        relations=[],
    )
    return output.model_dump(mode="json")
