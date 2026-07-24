from __future__ import annotations

from typing import Any


def validate_high_confidence_result(output: dict[str, Any]) -> None:
    decision = output.get("decision")
    if decision not in {"approved", "rejected", "needs_review", "abstain"}:
        raise ValueError("high-confidence decision is invalid")
    confidence = output.get("confidence")
    if not isinstance(confidence, int | float) or isinstance(confidence, bool):
        raise ValueError("high-confidence result requires numeric confidence")
    if not 0 <= float(confidence) <= 1:
        raise ValueError("high-confidence confidence must be between 0 and 1")
    if not isinstance(output.get("reason_codes"), list):
        raise ValueError("high-confidence result requires reason_codes")
    _validate_spans(output)


def deterministic_high_confidence(payload: dict[str, Any]) -> dict[str, Any]:
    text = str(payload.get("candidate_text") or "")
    evidence = str(payload.get("evidence_text") or text)
    candidate = text.strip()
    source = evidence.strip()
    decision = (
        "approved"
        if candidate and source and (candidate in source or source in candidate)
        else "needs_review"
    )
    return {
        "decision": decision,
        "confidence": 0.82 if decision == "approved" else 0.45,
        "reason_codes": [
            "deterministic_evidence_alignment"
            if decision == "approved"
            else "evidence_alignment_uncertain"
        ],
        "evidence_spans": [
            {"start": 0, "end": len(evidence), "text": evidence}
        ]
        if evidence
        else [],
    }


def validate_privacy_result(output: dict[str, Any]) -> None:
    if output.get("classification") not in {
        "normal",
        "sensitive",
        "secret",
        "abstain",
        "requires_review",
    }:
        raise ValueError("privacy classification is invalid")
    if not isinstance(output.get("reason_codes"), list):
        raise ValueError("privacy result requires reason_codes")
    _validate_spans(output)


def deterministic_privacy(payload: dict[str, Any]) -> dict[str, Any]:
    text = str(payload.get("candidate_text") or "")
    lowered = text.lower()
    secret_terms = {
        "api key",
        "password",
        "secret",
        "access token",
        "bearer token",
        "private key",
        "رمز",
        "گذرواژه",
        "کلید خصوصی",
        "توکن دسترسی",
    }
    sensitive_terms = {
        "medical",
        "health",
        "address",
        "phone",
        "email",
        "بیماری",
        "نشانی",
        "آدرس",
        "تلفن",
    }
    classification = "normal"
    reasons = ["deterministic_no_sensitive_indicator"]
    if any(term in lowered for term in secret_terms):
        classification = "secret"
        reasons = ["deterministic_secret_indicator"]
    elif any(term in lowered for term in sensitive_terms):
        classification = "sensitive"
        reasons = ["deterministic_sensitive_indicator"]
    return {
        "classification": classification,
        "reason_codes": reasons,
        "evidence_spans": (
            [{"start": 0, "end": len(text), "text": text}]
            if classification != "normal" and text
            else []
        ),
    }


def validate_compaction_result(output: dict[str, Any]) -> None:
    items = output.get("items")
    if not isinstance(items, list):
        raise ValueError("block compaction requires items")
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("block compaction items must be objects")
        if not isinstance(item.get("text"), str) or not str(item["text"]).strip():
            raise ValueError("compacted item requires non-empty text")
        if not isinstance(item.get("source_memory_uuids"), list):
            raise ValueError("compacted item requires source_memory_uuids")
        if not isinstance(item.get("source_memory_version_uuids"), list):
            raise ValueError("compacted item requires source_memory_version_uuids")
    summary = output.get("summary")
    if summary is not None and not isinstance(summary, str):
        raise ValueError("block compaction summary must be text")
    excluded = output.get("excluded_memory_version_uuids", [])
    if not isinstance(excluded, list) or any(not isinstance(item, dict) for item in excluded):
        raise ValueError("block compaction exclusions must be objects")
    conflicts = output.get("conflicts", [])
    if not isinstance(conflicts, list) or any(not isinstance(item, dict) for item in conflicts):
        raise ValueError("block compaction conflicts must be objects")


def deterministic_compaction(payload: dict[str, Any]) -> dict[str, Any]:
    sources = payload.get("sources")
    items: list[dict[str, Any]] = []
    sources_by_key: dict[str, list[dict[str, Any]]] = {}
    if isinstance(sources, list):
        for source in sources:
            if not isinstance(source, dict):
                continue
            sources_by_key.setdefault(str(source.get("canonical_key") or ""), []).append(
                source
            )
            items.append(
                {
                    "text": str(source.get("normalized_text") or ""),
                    "source_memory_uuids": [str(source.get("memory_uuid"))],
                    "source_memory_version_uuids": [
                        str(source.get("memory_version_uuid"))
                    ],
                }
            )
    conflicts = []
    for grouped_sources in sources_by_key.values():
        normalized_values = {
            str(source.get("normalized_text") or "") for source in grouped_sources
        }
        if len(grouped_sources) > 1 and len(normalized_values) > 1:
            conflicts.append(
                {
                    "memory_version_uuids": [
                        str(source.get("memory_version_uuid"))
                        for source in grouped_sources
                    ],
                    "status": "unresolved",
                }
            )
    return {
        "summary": None,
        "items": items,
        "excluded_memory_version_uuids": [],
        "conflicts": conflicts,
        "status": "ok",
    }


def validate_import_reconstruction_result(output: dict[str, Any]) -> None:
    if output.get("trust_level") != "historical_untrusted":
        raise ValueError("import reconstruction must remain historical_untrusted")
    if not isinstance(output.get("messages"), list):
        raise ValueError("import reconstruction requires messages")


def deterministic_import_reconstruction(payload: dict[str, Any]) -> dict[str, Any]:
    fragments = payload.get("fragments")
    messages = fragments if isinstance(fragments, list) else []
    return {
        "trust_level": "historical_untrusted",
        "messages": messages,
        "warnings": ["deterministic reconstruction used"],
    }


def _validate_spans(output: dict[str, Any]) -> None:
    spans = output.get("evidence_spans")
    if not isinstance(spans, list):
        raise ValueError("stage result requires evidence_spans")
    for span in spans:
        if not isinstance(span, dict):
            raise ValueError("evidence span must be an object")
        start = span.get("start")
        end = span.get("end")
        if not isinstance(start, int) or not isinstance(end, int) or start < 0 or end < start:
            raise ValueError("evidence span offsets are invalid")
