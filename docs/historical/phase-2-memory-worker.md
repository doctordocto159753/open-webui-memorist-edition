# Phase 2 memory worker

Phase 2 adds a local, evidence-grounded Memory Worker path. LLM output is still treated as candidate material, not accepted truth.

## Implemented

- Phase 2 SQLite schema for processing runs, text units, gate decisions, analyses, candidates, evidence, memories, memory versions, consolidation decisions, evidence links, relations, and graph projection outbox.
- Deterministic text unitization with exact Python string offsets.
- Deterministic memory gating with fail-safe optional classifier handling.
- Provider-neutral structured analysis interfaces with application-side validation.
- Rule-based candidate extraction with exact evidence validation.
- Explicit consolidation operations with temporal memory versions.
- Disabled/FalkorDB graph projector interfaces with a local SQLite outbox.
- Audit/read endpoints for processing runs, lineage, candidates, memories, versions, evidence, and graph projection.
- Versioned system-prompt registry under `memcore.memory_worker.prompts` for unit analysis, candidate extraction, consolidation assistance, preflight planning, block compaction, import reconstruction, contradiction detection, and privacy sensitivity classification.
- I-JSON prompt-output validation that rejects missing evidence, assistant speculation promoted as user memory, prompt mutation during preflight, block compaction without source UUIDs, trusted imported content, and unrestricted high-sensitivity retrieval.
- Local prompt invocation tracking with prompt/model/provider identifiers and canonical input/output hashes.

## Not implemented

- Real LLM provider calls.
- Fully automated model-driven Active Memory Block generation.
- Fully automated model-driven import reconstruction.
- Automatic forgetting or decay.
- Community detection or autonomous prompt rewriting.

Retrieval, reranking, preflight injection, and Memory Context Attachment building are introduced in Phase 3.
