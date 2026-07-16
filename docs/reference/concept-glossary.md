# Concept Glossary

This glossary freezes baseline terminology before the final four implementation steps.

- `Raw Message`: the original user, assistant, system, or tool message captured from Open WebUI or import. It is preserved as source evidence.
- `Text Unit`: the current baseline segmentation unit with offsets. It may contain a sentence, paragraph fragment, or compact utterance depending on unitization.
- `Sentence Unit`: the Step 1 first-class sentence-level unit. It has exact offsets and must not be confused with broader legacy text units.
- `Jakobson Annotation`: the implemented Step 1 sentence-level six-factor annotation over a Sentence Unit. It is now the primary baseline semantic pipeline.
- `Memory Signal Route`: the implemented Step 1 routing decision from Jakobson annotation to specialized extraction path.
- `Memory Candidate`: an evidence-backed proposed memory that has not yet become canonical memory.
- `Memory`: the canonical local memory identity.
- `Memory Version`: the temporal/auditable version of a Memory. Corrections and updates create versions rather than rewriting history.
- `Memory Context Attachment`: bounded untrusted memory context rendered separately from the user prompt for Open WebUI preflight.
- `Canonical Store`: the authoritative persistence layer. SQLite is canonical in Lite; PostgreSQL is canonical in Full.
- `Projection`: a derived index or graph view built from canonical state. A projection is never the source of truth.
- `Lite Mode`: the currently supported local baseline using SQLite and local object paths.
- `Full Mode`: the PostgreSQL canonical store plus durable PostgreSQL jobs/outboxes, Hot Scheduler runnable references, and FalkorDB projection/runtime profile. All `full_mode_check.py` gates passed in the tested local Docker environment.
- `Model Control Plane`: the role/profile/default/usage/privacy surface that separates Open WebUI’s main chat model from Memorist memory roles.
- `Prompt Pack`: a versioned set of system prompts, input schemas, output schemas, validators, evidence rules, rejection rules, role mappings, and timeout metadata for non-chat memory worker nodes. Prompt Pack v2 is current baseline.
- `Prompt Execution`: a local auditable invocation of a prompt against input data with prompt/model/provider metadata, scope links, input/output hashes, raw/validated output references, warnings, sanitized error, latency, and token counts.
- `Hot Scheduler`: the Full Mode in-memory scheduler for runnable job references. It protects live chat and privacy lanes; PostgreSQL jobs/outboxes remain durable.

## Legacy Labels

- `memorist.unit_analysis` is retained as a legacy derived summary. It is not the primary `memorist.jakobson_sentence_analysis` prompt.
- Placeholder smoke scripts are documentation markers only and are not release evidence.
- Manual-only smoke tests require operator action and are not counted as automated beta gates.
