Prompt ID: memorist.poetic_style_extractor
Prompt Version: 2.0
Allowed model role: memory_extraction

Extract stylistic, branding, slogan-like, rhetorical-form, and tone preferences only when form is foregrounded.
Do not mistake ordinary fluent writing for a durable style preference.
Preserve examples as evidence. Do not invent style rules.

Allowed candidate_type values: style_policy, branding_style, slogan_preference, rhetorical_pattern.
Each accepted item must include annotation_uuid, route_uuid, canonical_key, subject, predicate, object, value, natural_language_summary, confidence, importance, evidence, rejection_reason.
Each evidence span must include annotation_uuid, route_uuid, unit_uuid, message_uuid, quote, span_start, span_end.

Input payload:
{{PAYLOAD_IJSON}}
