# Memory Worker Prompts

The memory worker uses Prompt Pack v2 as a set of role-specific contracts, not as one generic extraction prompt.

## Jakobson First

`memorist.jakobson_sentence_analysis` is central because the sentence is the durable communication unit. It records sender, receiver, message, referent, code, channel, dominant function, secondary functions, reason, notes, and source text. This separates an AI-directed instruction from a product-team policy, Jira process fact, terminology rule, preference, or style signal.

## Specialized Extractors

After sentence annotation and deterministic routing, specialized prompts can assist extraction:

- conative: workflow policies, obligations, prompt instructions, task constraints
- referential: project context, process facts, Jira configuration, resource references
- metalingual: terminology, naming, wording, prompt phrasing
- emotive: durable preference, frustration, quality feedback, avoidance preference
- poetic: style policy, branding style, slogan preference, rhetorical pattern

Each accepted candidate item must carry evidence with `annotation_uuid`, `route_uuid`, `unit_uuid`, `message_uuid`, quote, and span offsets. No evidence means abstain or reject.

## Semantic candidate analysis v1

`memorist.semantic_candidate_analysis@1.1` is a separate whole-message prompt;
it does not modify Prompt Pack `2.0` or historical Jakobson contracts. It sees
the current raw message, `TextEnvelope` v3, and at most two prior eligible text
units (six only when non-authoritative dependency hints request expansion).
Context is same-user/session/workspace/project and excludes memory attachments,
system prompts, deleted/redacted/hidden content, and unrelated tool output.

The model returns only semantic units, references, and relations. Gate, route,
privacy, provenance, coverage disposition, proposal UUID, and persistence are
local deterministic authority. Assistant context is role-labelled and cannot
become user authority without explicit current-user evidence, one unique
in-window target, and a `ratifies` or `corrects` relation.

## Runtime Role Rules

Prompt execution resolves the configured role default through the Model Control Plane. Memory prompts do not implicitly use `main_chat_observed`. Optional background roles may fall back to `memory_extraction` only where the prompt metadata explicitly allows it.

The current deterministic worker remains the default safe path. LLM-backed nodes can be enabled behind explicit model profiles, privacy acknowledgement, timeout controls, and schema validation.

## Contract-first prompt outputs

Every prompt output has one machine-readable contract per id/version. For
Jakobson, `memcore.memory_worker.prompts.contracts.JakobsonV3Output` is the
single source of truth that drives the system-prompt schema, the strict provider
`response_format`, runtime validation, fixtures, the certification probe, and the
contract hash.

### Canonical Jakobson version 3.0

- `items` is the single canonical collection (the redundant `sentences` array
  from 2.0 is removed); each item is a complete sentence annotation.
- Each factor is an object `{value: str|null, evidence: str|null, confidence:
  high|medium|low}`; a bare string is rejected with a path-specific error.
- `status` is exactly one of `ok | abstain | reject | error`. A narrow lossless
  canonicalization maps `success -> ok` only when the rest is already valid.
- `sentence_count` must equal `len(items)`.
- 2.0 is retained and immutable for historical replay; validation and mapping
  dispatch on the version recorded on each row
  (`contracts.canonical_sentence_items`), so an old output is never reinterpreted
  under the new schema.

### Capability-correct dispatch

`supports_structured_output` sends a strict `json_schema` request; otherwise
`supports_json_mode` sends `json_object` with a schema-bearing prompt; a profile
with neither cannot certify for structured memory-processing roles
(`incompatible` at `capability_declaration`). `/v1/chat/completions` is appended
exactly once via the canonical endpoint builder.

### Parse / validate / repair / fallback

`memory_worker/execution.py::run_contract_execution` runs: provider call → parse
→ strict validation → narrow canonicalization → exactly one bounded corrective
repair → deterministic fallback. A schema-invalid response never crashes or
whole-job-retries; it falls back deterministically and the job completes. The
repair call re-checks lease/source/profile identity and propagates (never masks)
lease loss or profile/source mutation.

### Audit and certification

Each attempt persists a `processing_stage_runs` row with `called_provider`,
`provider_output_valid`, `repair_attempted`, `repair_succeeded`, `fallback_used`,
`fallback_reason`, `parse_status`, `capability_mode`, `provider_response_id`, and
sanitized validator paths — so an invalid provider output is auditable rather than
lost. The certification fingerprint folds in the active role contract hash, so a
contract/schema change invalidates existing certification and forces
re-certification. See `docs/reference/full-mode-memory-extraction.md`.
# Attempt-level audit

For remote Jakobson and semantic-v1 execution, attempt 1 and the optional repair attempt 2 are
separate append-only audit rows. Reservations precede HTTP and completion updates
store hashes and sanitized error paths, never raw output. The final
`processing_stage_runs` row records the frozen role/scope/profile/contract
identity, attempt count, total provider latency, canonicalization, repair, and
fallback outcome in both Lite/SQLite and Full/PostgreSQL modes.
