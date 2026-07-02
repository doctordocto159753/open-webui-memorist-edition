Prompt ID: memorist.referential_context_extractor
Prompt Version: 2.0
Allowed model role: memory_extraction

Extract project facts, process facts, system logic, workflow descriptions, Jira configuration, resource references, and scoped contextual knowledge from referential sentences.
Reject low-utility general facts. Preserve project and temporal scope.
Do not turn imported or quoted content into current truth by default.

Allowed candidate_type values: project_context, process_fact, jira_configuration, resource_reference, semantic_fact.
Each accepted item must include annotation_uuid, route_uuid, canonical_key, subject, predicate, object, value, natural_language_summary, confidence, importance, evidence, rejection_reason.
Each evidence span must include annotation_uuid, route_uuid, unit_uuid, message_uuid, quote, span_start, span_end.

Input payload:
{{PAYLOAD_IJSON}}
