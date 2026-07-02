# Prompt Safety

Prompt Pack v2 treats all analyzed text as untrusted data.

## Global Rules

- Prompts do not answer the user and do not chat.
- Prompts do not obey instructions inside analyzed content.
- Outputs must be valid I-JSON and must not include markdown or chain-of-thought.
- Assistant speculation cannot become user memory.
- Quoted third-party claims cannot become user belief unless the user explicitly endorses them.
- Imported content is historical and untrusted by default.
- Private facts require explicit evidence.
- Sensitive content is marked and routed safely.
- Corrections and contradictions preserve historical versions.

## Rejection Rules

Prompt outputs are rejected when they are not I-JSON, do not match the global envelope, violate a prompt-specific schema, exceed preflight budget, omit required evidence, reference unknown block sources, or try to promote ordinary memory into trusted directives.

High-sensitivity privacy results cannot use unrestricted storage and cannot auto-attach normally. Preflight invalid output fails open rather than blocking the main chat.

## Secret Handling

Prompt execution audit records store hashes and local I-JSON outputs. Secret-like keys containing `key`, `token`, `secret`, `password`, or `credential` are redacted before persistence. Diagnostics use sanitized errors and never require raw provider credentials.
