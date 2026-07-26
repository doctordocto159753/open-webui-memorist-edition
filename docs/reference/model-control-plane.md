# Processing-node runtime and Model Control Plane

Memorist keeps the model selected for the visible Open WebUI chat separate
from the models that process memory. A **role** names one processing
responsibility. A **profile** names a provider, model, endpoint, capabilities,
and an optional secret environment-variable reference. A role default connects
the two at project, workspace, or global scope.

The runtime never silently borrows the Open WebUI chat model. Every processing
stage resolves a role, records the effective profile or local fallback, and
writes an auditable stage result.

## Stage graph and role ownership

```text
Open WebUI user turn
  ├─ main_chat_observed       metadata only; owned by Open WebUI
  └─ preflight                bounded planning before the main request
       └─ fail open: chat continues without memory on error/timeout

Captured user/assistant evidence
  └─ memory_extraction        sentence analysis, gate, route, candidates
       ├─ privacy_sensitivity sensitive-content classification
       ├─ high_confidence_extraction second pass for consequential claims
       └─ embedding           asynchronous canonical/query vectors

Historical import evidence
  └─ import_reconstruction    untrusted reconstruction/extraction pass

Versioned active-memory sources
  └─ block_compaction         model-assisted compaction under deterministic
                              coverage and provenance constraints
```

`memory_extraction`, privacy, high-confidence, import, and compaction outputs
must be valid structured JSON and pass their typed validators before they can
affect local state. Prompt input/output hashes, raw and validated output,
provider/model, token counts, latency, fallback reason, and source identifiers
are recorded in `prompt_execution_runs` and `processing_stage_runs`.

The compaction model is advisory: the deterministic materializer remains the
coverage and ordering authority so a plausible model response cannot silently
drop an active constraint. Import output remains untrusted historical evidence
and uses the ordinary gate/provenance pipeline before consolidation.

## Resolution and inheritance

Every runtime call uses the same resolver and the same precedence:

| Priority | Match |
| --- | --- |
| 1 | project default for the requested role |
| 2 | workspace default for the requested role |
| 3 | global default for the requested role |
| 4 | the same scoped search for a documented inherited role |
| 5 | built-in local deterministic or disabled fallback |

Documented inheritance is deliberately small:

| Requested role | Inherits when not configured |
| --- | --- |
| `high_confidence_extraction` | `memory_extraction` |
| `privacy_sensitivity` | `memory_extraction` |
| `import_reconstruction` | `memory_extraction` |
| `block_compaction` | `memory_extraction` |

`preflight` and `embedding` do not inherit another provider. The embedding
built-in fallback is disabled because lexical/FTS retrieval is the truthful
no-vector path. `main_chat_observed` is metadata-only and controlled by Open
WebUI.

A configured profile is skipped when it is disabled, assigned to the wrong
role, missing a required secret reference, missing remote privacy
acknowledgement, missing/current-stale certification, or incompatible with the requested capability. The resolution
response states `requested_role`, `effective_role`, `scope_source`,
`inheritance_source`, `fallback_reason`, capability/secret/acknowledgement
status, and `resolution_version`. This is exposed by:

```http
GET /memcore/model-control/effective
GET /api/v1/memorist/model-control/effective
```

The authenticated Open WebUI proxy supplies the actor's workspace. Project
scope is included when a project is known.

## Provider endpoint and secret rules

For OpenAI-compatible profiles, enter any one of:

- the provider root, such as `https://provider.example`;
- its API base, such as `https://provider.example/v1`;
- a copied operation URL ending in `/chat/completions`, `/embeddings`, or
  `/models`.

Memorist adds `/v1` only to an origin-only URL. Explicit reverse-proxy prefixes
such as `/tenant/openai` are preserved, while terminal operation paths are
removed and appended exactly once. Duplicated or non-terminal operation paths
are rejected as ambiguous.
URLs without `http`/`https`, URLs with credentials, query strings, or fragments
are rejected. Do not put an API key in the URL.

The browser and database store only a secret environment-variable **name**.
The value belongs in the Memorist Core process/container environment (normally
the package `.env`), is resolved immediately before a request, and is never
returned by the API. Remote profiles require an explicit privacy
acknowledgement before default assignment because role-specific conversation,
candidate, memory, or import text may leave the machine.

## Setup and duplicate protection

The primary admin path is:

```text
Settings → Memorist → Memory Setup / Processing Nodes
```

The setup wizard creates all seven processing roles, saves a stable
`setup_idempotency_key`, prevents duplicate active submissions, tests the
actual role capability, persists a fingerprinted certification, and assigns a default only after the test reports both
`overall_status=ok` and `role_compatibility_status=compatible`. It then reads
effective state back and verifies that the intended profile is really active.
Replaying a save or a stage invocation with the same logical idempotency key
returns the existing record instead of duplicating profiles, calls, usage
events, or candidate transitions.

## Truthful provider test contract

The test timeout is configurable with
`MEMORIST_PROVIDER_TEST_TIMEOUT_MS` (default 60000 ms). It is independent of
interactive preflight, capture, ordinary control-plane, diagnostics, and import
timeouts. Chat roles call
`POST /v1/chat/completions`; embedding calls `POST /v1/embeddings`.
`GET /v1/models` is optional metadata and never the success gate. If JSON or
structured output is declared, the probe uses a strict schema with the required
constant marker and no additional properties. JSON-object-only providers
receive an exact-output instruction and at most one corrective retry. A valid
JSON response with the wrong marker is reported separately from malformed JSON
or transport failure.

The result reports independent levels:

- DNS/host and TCP/HTTP reachability;
- authentication;
- model availability;
- chat or embedding operation support;
- structured-output support;
- role compatibility;
- overall status, HTTP status, retryability, quota/rate-limit state;
- sanitized detail and a recommended operator action.

Important distinctions:

| Observation | Reported meaning |
| --- | --- |
| 401/403 | reachable, authentication failed |
| 404 | reachable, model/operation not found or incompatible |
| 429 | reachable, rate limited/quota constrained, retryable |
| 5xx | reachable, provider error, retryable |
| timeout | timed out; not misreported as bad credentials |
| dropped/refused connection | unreachable transport |
| ordinary text or malformed JSON | connected but role-incompatible |
| JSON-mode rejection | connected but structured output unsupported |
| missing env-var value | locally misconfigured; no provider call |
| wrong embedding dimension | connected but embedding-incompatible |

Provider response details are sanitized before persistence or display.
Credentials, bearer tokens, query secrets, and raw environment values must not
appear in health rows, logs, UI errors, or stage traces.

## Runtime behavior and failure policy

- `preflight` is bounded and fail-open. Provider failure produces no memory
  attachment for that turn but never blocks the Open WebUI main response.
- post-response extraction, privacy, high-confidence, embedding, import, and
  compaction run off the main request. Failed external calls use the role's
  validated deterministic fallback or record a skipped/failed-open result.
- embedding writes are outbox-driven. Vectors store their effective profile,
  provider model, and dimension; configured vectors are consumed for semantic
  retrieval. A profile change can stale/rebuild the projection. FTS remains
  available when the embedding role is disabled or unavailable.
- substantial structured project artifacts can be captured as one exact
  evidence-backed episode. User-authored artifacts use `user_explicit`;
  assistant-produced artifacts use `assistant_claim`, carry
  `not_user_fact=true`, link to the preceding user request, and require the
  high-confidence second pass. They are never relabelled as user facts.

Lite/SQLite and Full/PostgreSQL use the same resolver, validators, stage
contract, provenance rules, and diagnostic response. Their historical safe
automatic candidate labels differ (`ready_for_consolidation` in Lite,
`accepted` in Full); diagnostics normalize both to
`ready_for_consolidation` under
`processing-candidate-lifecycle-v1` while retaining `candidate_statuses_raw`
for audit. `needs_review` and `rejected` never transition automatically into
canonical memory.

## Runtime diagnostics

For a processing run, use:

```http
GET /memcore/memory-processing/runs/{processing_run_uuid}/stages
GET /api/v1/memorist/memory-processing/runs/{processing_run_uuid}/stages
```

The secret-free response includes capture/processing state; text-unit, route,
gate, candidate, and memory counts; canonical and raw candidate statuses; each
provider/fallback stage; retryable failures; and whether an external provider
was called. If no canonical memory was produced, `no_memory_reason`
distinguishes no eligible signal, review, rejection, and consolidation with no
result.

Other developer APIs:

```http
GET  /memcore/model-control/roles
GET  /memcore/model-control/profiles
POST /memcore/model-control/profiles
PATCH /memcore/model-control/profiles/{model_profile_uuid}
POST /memcore/model-control/profiles/{model_profile_uuid}/test
GET  /memcore/model-control/defaults
POST /memcore/model-control/defaults
DELETE /memcore/model-control/defaults?role={role}
GET  /memcore/model-control/usage
GET  /memcore/model-control/health
GET  /memcore/model-control/privacy
POST /memcore/model-control/privacy/acknowledge
POST /memcore/model-control/estimate-cost
GET  /memcore/costs/model-roles
```

## Troubleshooting checklist

1. **Profile saved but fallback is effective:** inspect `/effective`.
   `fallback_reason` identifies disabled profile, missing secret,
   acknowledgement, role mismatch, or missing capability.
2. **Secret environment variable is not set:** add the named variable to the
   Core container/process `.env` and restart. Do not paste the value into the
   profile form.
3. **Connection works in a browser but Test is unreachable:** the URL must be
   reachable from `memorist-core`; for a host service under Docker Desktop,
   use `host.docker.internal` rather than `localhost`.
4. **Wrong path or duplicated `/v1`:** save the provider root, `/v1` base,
   custom reverse-proxy base, or full operation URL. The normalizer preserves
   custom prefixes, removes one terminal operation suffix, and rejects an
   already duplicated operation path.
5. **401/403:** check the env-var name, its value in the running Core
   environment, and provider permissions. This is not a network failure.
6. **404 or model unavailable:** use the exact model ID and confirm whether the
   endpoint implements chat completions or embeddings for that model.
7. **429:** wait/retry or check quota; do not change a correct API key merely
   because the provider is rate limiting.
8. **JSON/structured output incompatible:** disable an incorrectly declared
   capability or choose a model that accepts JSON mode and returns the marker.
9. **Embedding dimension mismatch:** update `embedding_dimension` to the
   provider's real vector size, then rebuild stale embeddings.
10. **A captured message created no memory:** open its processing-run stage
    trace and read `no_memory_reason`; greetings, weak signals, privacy paths,
    rejected claims, and review-required candidates correctly produce none.
11. **Provider failed but chat still answered:** expected fail-open behavior;
    inspect the trace for `fallback_used` or a retryable failure, and inspect
    Settings → Memorist → Diagnostics for the sanitized per-stage degraded
    outcome. The degraded flag clears only after that stage succeeds.

See also [architecture](../ARCHITECTURE.md),
[the memory machine](../MEMORY_MACHINE.md), and
[security](../../SECURITY.md).
# Role-contract manifests and certification

`memcore.model_control.role_contracts` is the authority for the prompt,
version/schema, required capability, fallback policy, and certification probe of
each processing role. Certification fingerprints include the complete manifest
hash. In particular, `memory_extraction` uses Jakobson v3 while
`import_reconstruction` uses its own v2 prompt; they are not interchangeable.
The observed Open WebUI main-chat role is explicitly non-certifiable.
