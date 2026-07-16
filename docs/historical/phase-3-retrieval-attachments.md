# Phase 3 retrieval and Memory Context Attachment

Phase 3 adds the local pre-send memory path:

message -> retrieval plan -> candidate generation -> temporal/conflict-aware ranking -> evidence selection -> Memory Context Attachment -> Open WebUI pre-send injection.

SQLite remains authoritative. Graph and embedding backends remain optional and local-first; Lite mode works with SQLite only.

## Implemented

- Auditable retrieval runs, generated queries, candidates, FTS index state, embedding metadata, preflight events, and assistant response links.
- Deterministic retrieval planner with query intents, entity extraction, temporal hints, memory type hints, and server-side scope contracts.
- Local hybrid candidate generation:
  - exact canonical-key matches;
  - active project/workspace constraints;
  - SQLite FTS5 BM25;
  - deterministic local semantic embeddings for non-Lite modes;
  - recent session memories;
  - safe graph stub fallback.
- Reciprocal Rank Fusion with per-generator trace preservation.
- Explainable deterministic scoring with temporal, scope, authority, confidence, importance, evidence, conflict, graph, and sensitivity components persisted on candidates.
- Selection policy with diversity filtering and explicit abstention.
- Memory Context Attachment builder with bounded rendering, provenance, source UUIDs, trust separation, delimiter escaping, and instruction-like content detection.
- Preflight endpoint with fail-open/disabled/timeout statuses and local event recording.
- Open WebUI filter stub using `inlet()` for safe pre-send injection and `outlet()` for assistant-response completion linking.
- Deduplicated assistant response capture using provider response IDs or content hashes.

## Endpoints

- `POST /memcore/retrieval/plan`
- `POST /memcore/retrieval/run`
- `GET /memcore/retrieval/runs/{retrieval_run_uuid}`
- `GET /memcore/retrieval/runs/{retrieval_run_uuid}/candidates`
- `POST /memcore/attachments/build`
- `GET /memcore/attachments/{attachment_uuid}`
- `GET /memcore/attachments/{attachment_uuid}/sources`
- `POST /memcore/preflight`
- `POST /memcore/assistant-response/completed`

## Preflight config

- `MEMORIST_PREFLIGHT_ENABLED=true`
- `MEMORIST_PREFLIGHT_TIMEOUT_MS=500`
- `MEMORIST_RETRIEVAL_MODE=standard`
- `MEMORIST_ATTACHMENT_TOKEN_BUDGET=1800`
- `MEMORIST_FAIL_OPEN=true`

## Not implemented

- Import wizard.
- Automatic memory-block compaction.
- Autonomous prompt rewriting.
- Automatic destructive forgetting.
- Multi-user cloud service.
- Opaque agentic retrieval loops.
- Graph community summarization.
- Real external LLM/embedding provider calls.

## Evaluation baseline

The Phase 3 tests cover deterministic plans, FTS consistency, exact technical identifiers, semantic paraphrase retrieval, Persian memory/query handling, Lite mode without embeddings, embedding invalidation, graph fallback, scope-leak prevention, current-vs-historical selection, safe attachment rendering, active constraint trust separation, fail-open preflight, and assistant completion dedupe.
