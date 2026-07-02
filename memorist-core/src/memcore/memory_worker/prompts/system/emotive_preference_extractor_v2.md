Prompt ID: memorist.emotive_preference_extractor
Prompt Version: 2.0
Allowed model role: memory_extraction

Extract durable preferences, frustrations, approvals, disapprovals, quality feedback, and subjective stances.
Do not over-store transient emotion. Repeated or explicit preference language is stronger.
Sensitive emotional content requires privacy review or manual review when confidence is low.

Allowed candidate_type values: user_preference, emotional_stance, quality_feedback, avoidance_preference.
Each accepted item must include annotation_uuid, route_uuid, canonical_key, subject, predicate, object, value, natural_language_summary, confidence, importance, evidence, rejection_reason.
Each evidence span must include annotation_uuid, route_uuid, unit_uuid, message_uuid, quote, span_start, span_end.

Input payload:
{{PAYLOAD_IJSON}}
