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


## Admin UI setup

The primary operator path for Model Control configuration is the Open WebUI admin surface:

```text
Settings → Memorist → Processing Nodes
```

Use this screen to create and manage processing-node profiles, test real role capability, acknowledge privacy for remote endpoints, and assign role defaults. For OpenAI-compatible processing nodes, provide the node name, provider type, `/v1` base endpoint URL, model name, endpoint locality, environment-variable secret strategy, secret environment variable name, and JSON/structured-output capability flags. FreeLLMAPI is an example of a generic OpenAI-compatible endpoint, not a dedicated provider path.

Profiles can be edited from the processing-node list and should be tested before assignment. **Test** validates the configured role capability, not just endpoint reachability: LLM roles are exercised with `POST /v1/chat/completions`, and the `embedding` role is exercised with `POST /v1/embeddings`. `GET /v1/models` is optional diagnostic metadata only and is not the success gate. When `supports_json_mode` or `supports_structured_output` is enabled, the test actively verifies JSON response support with `response_format: {"type": "json_object"}`. Remote endpoints must receive a privacy acknowledgement before they can become defaults. Role defaults are assigned from **Settings → Memorist → Processing Nodes → Role defaults**; for Full Mode extraction, assign the tested profile to `memory_extraction`.

The HTTP APIs below remain available for developers, automation, and troubleshooting, but they are not the primary setup path once the admin UI is implemented.

## Developer APIs

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

Remote profiles must be acknowledged before default assignment. Secrets are environment references only; the named secret environment variable must exist in the Memorist backend container/process environment. Raw API keys are rejected, are not persisted, and are not returned by Model Control APIs.
