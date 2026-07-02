# Model Control Plane

The Model Control Plane keeps Open WebUI chat ownership separate from Memorist memory infrastructure.

## Roles

- `main_chat_observed`: selected in Open WebUI; Memorist observes metadata only.
- `preflight`: bounded pre-main-request planning role; validates `memorist.preflight_planning`; fail-open.
- `memory_extraction`: asynchronous post-response worker role; drives Jakobson analysis and candidate extraction.
- `embedding`: independent semantic indexing role; disabled by default in Lite and re-indexable after profile changes.
- `import_reconstruction`, `high_confidence_extraction`, `block_compaction`, `privacy_sensitivity`: optional background roles that inherit local-safe defaults until configured.

## Runtime Flow

```text
Open WebUI selected model
  -> Memorist observes main_chat_observed metadata
User message captured
  -> deterministic retrieval and budget calculation
  -> preflight profile resolved
  -> optional provider output validated
  -> Memory Context Attachment inserted separately
Assistant response captured
  -> memory_extraction job queued
  -> worker resolves memory_extraction profile
  -> sentence-level Jakobson pipeline persists evidence
Memory/query text
  -> embedding profile resolved
  -> embedding record stores profile UUID and dimension
  -> embedding default changes mark old records stale
```

## APIs

```http
GET  /memcore/model-control/roles
GET  /memcore/model-control/profiles
POST /memcore/model-control/profiles
PATCH /memcore/model-control/profiles/{model_profile_uuid}
POST /memcore/model-control/profiles/{model_profile_uuid}/test
GET  /memcore/model-control/defaults
POST /memcore/model-control/defaults
GET  /memcore/model-control/usage
GET  /memcore/model-control/health
GET  /memcore/model-control/privacy
POST /memcore/model-control/privacy/acknowledge
POST /memcore/model-control/estimate-cost
GET  /memcore/costs/model-roles
```

Remote profiles must be acknowledged before default assignment. Secrets are environment references only; raw keys are rejected.
