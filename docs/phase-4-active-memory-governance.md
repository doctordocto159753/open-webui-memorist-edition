# Phase 4 active memory, feedback, correction, and privacy governance

Phase 4 adds governance around memory after canonical extraction:

canonical memories -> versioned Active Memory Blocks -> bounded always-in-context state -> usage and attribution logging -> user correction -> dependency invalidation -> verified forgetting/erasure.

## Implemented

- Active Memory Block build runs, immutable block versions, source mappings, optimistic lock updates, rollback, coverage, diff, compaction, and stale rebuild.
- Deterministic block materialization from current canonical memory versions and session hot cache; previous block text is never summarized into the next block.
- Block policies for `UserProfileBlock`, `ProjectContextBlock`, `StylePolicyBlock`, `PromptRulesBlock`, `CurrentSessionStateBlock`, and read-only `SafetyPrivacyBlock`.
- Delivery events that distinguish `retrieved`, `selected`, `rendered`, `injected`, and `excluded`.
- Response-memory attributions with explicit statuses such as `supported`, `unused`, and `unclear`; model self-report is not treated as proof.
- User feedback logging with follow-up change requests or privacy requests for outdated/incorrect/privacy feedback.
- Memory inspection with versions, evidence, usage, and dependent block links.
- Change requests for confirmation, correction, outdated/incorrect/retract, restore/undo, and dependency invalidation.
- Privacy preview/confirm/execute/retry workflow with adapter-based discovery, quarantine, erasure, verification, and non-content-bearing receipts.

## Endpoints

- `POST /memcore/blocks/{block_uuid}/build`
- `GET /memcore/blocks/{block_uuid}/versions`
- `GET /memcore/blocks/{block_uuid}/sources`
- `POST /memcore/blocks/{block_uuid}/rollback/{version_number}`
- `POST /memcore/blocks/{block_uuid}/compact`
- `POST /memcore/blocks/rebuild-stale`
- `GET /memcore/blocks/{block_uuid}/coverage`
- `GET /memcore/blocks/{block_uuid}/diff`
- `GET /memcore/responses/{message_uuid}/memory-trace`
- `POST /memcore/memory-feedback`
- `GET /memcore/memories/{memory_uuid}/usage`
- `GET /memcore/attachments/{attachment_uuid}/delivery`
- `GET /memcore/memories/{memory_uuid}/inspect`
- `POST /memcore/memories/{memory_uuid}/change-requests`
- `GET /memcore/memory-change-requests/{request_uuid}`
- `POST /memcore/memory-change-requests/{request_uuid}/apply`
- `POST /memcore/memory-change-requests/{request_uuid}/cancel`
- `POST /memcore/memory-change-requests/{request_uuid}/undo`
- `POST /memcore/privacy/requests/preview`
- `POST /memcore/privacy/requests/{request_uuid}/confirm`
- `POST /memcore/privacy/requests/{request_uuid}/execute`
- `GET /memcore/privacy/requests/{request_uuid}`
- `GET /memcore/privacy/requests/{request_uuid}/receipt`
- `POST /memcore/privacy/requests/{request_uuid}/retry`

## Erasure notes

The privacy workflow is technical and policy-configurable; it does not hardcode legal conclusions. Execution quarantines memories before slower cleanup, removes FTS rows, removes embeddings, invalidates blocks and hot cache entries, redacts affected attachments, checkpoints WAL, and writes receipts without erased content.

SQLite physical deletion has limits: deleted bytes may remain in free pages depending on `secure_delete`, WAL state, filesystem behavior, storage media, and backups. High-assurance transactions enable `PRAGMA secure_delete=ON`; `VACUUM` is intentionally not run on the hot path and should be scheduled for maintenance windows when required.

Backups may still contain pre-erasure data until backup retention expires. A restore procedure must replay completed erasure receipts/ledger entries before serving restored data.

## Not implemented

- Legal decision automation.
- Real FalkorDB deletion calls; the adapter records disabled/external status.
- Object-store file discovery beyond the adapter placeholder.
- LLM compaction by default; invalid LLM compaction output falls back to deterministic logic.
- Cryptographic erasure; no key-destruction design exists in this phase.
