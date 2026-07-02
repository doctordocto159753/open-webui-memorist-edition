Prompt ID: memorist.privacy_sensitivity
Prompt Version: 2.0
Allowed model roles: privacy_sensitivity, memory_extraction fallback

Classify privacy sensitivity before storing or auto-attaching memory.
High sensitivity cannot auto-attach by default. Remote processing increases risk.
Do not expose sensitive raw content in diagnostics. Sensitivity classification does not delete content.

Allowed sensitivity_level values: none, low, medium, high.
Allowed allowed_storage values: allow, allow_local_only, manual_review, reject.
Allowed allowed_retrieval values: normal, restricted, never_auto_attach.
Return only the standard I-JSON envelope.

Input payload:
{{PAYLOAD_IJSON}}
