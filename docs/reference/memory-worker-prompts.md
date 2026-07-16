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

## Runtime Role Rules

Prompt execution resolves the configured role default through the Model Control Plane. Memory prompts do not implicitly use `main_chat_observed`. Optional background roles may fall back to `memory_extraction` only where the prompt metadata explicitly allows it.

The current deterministic worker remains the default safe path. LLM-backed nodes can be enabled behind explicit model profiles, privacy acknowledgement, timeout controls, and schema validation.
