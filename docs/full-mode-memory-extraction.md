# Full Mode memory extraction

Full Mode keeps PostgreSQL as the canonical store. FalkorDB is projection-only and receives changes from `graph_projection_outbox`.

## Capture pipeline

The Open WebUI capture adapter writes sessions and messages into PostgreSQL. Capture stops at durable message storage; it does not imply that memory extraction has run.

## Deterministic smoke extraction

The old PostgreSQL smoke helper is retained only as an explicit helper named `_pg_process_message_smoke`. Its rows use `full-smoke-v1` and `deterministic-full-smoke` and should not be used as the production `/memcore/memory-worker/process-message/{message_uuid}` path.

## API-backed structured extraction

The production Full/Postgres process-message route now uses `memory-intelligence-v2` and `memorist-memory-worker-prompt-pack-v2`. It resolves the `memory_extraction` model-control default, calls an OpenAI-compatible provider for profiles with `provider_type=openai_compatible` or `openai_compatible_llm`, validates structured JSON through the Prompt Pack v2 Jakobson schema, and records `prompt_execution_runs` plus `model_usage_events`.

A local OpenAI-compatible endpoint such as FreeLLMAPI can be configured with a model profile using an endpoint URL like:

```text
http://host.docker.internal:31415/v1
```

Secrets must be referenced through `secret_env_var_name`; raw API keys must not be stored in profile JSON. Diagnostics redact authorization and token-like values before persistence.

## Deterministic fallback

If no memory extraction profile exists, or the configured provider is disabled, Full/Postgres process-message uses deterministic Prompt Pack-compatible extraction. This fallback still uses the production pipeline version, records prompt execution rows with `provider_type=deterministic`, and remains idempotent.

## Graph projection

Memory extraction writes pending `memory_upserted` events to `graph_projection_outbox`. Projection runners consume those events and update FalkorDB. PostgreSQL remains canonical.

## Local fake provider smoke test

CI and local smoke tests can run `tests/support/fake_openai_provider.py`, which exposes an OpenAI-compatible `/v1/chat/completions` endpoint and returns deterministic structured JSON matching the Prompt Pack v2 Jakobson schema.

## Configuring API-backed extraction through Model Control

In Full Mode, the Model Control API writes profiles and defaults to PostgreSQL, the same canonical tables read by `PostgresMemoryWorkerPipeline`. Normal setup does not require manual SQL inserts.

Example OpenAI-compatible/FreeLLMAPI profile:

```bash
curl -X POST http://localhost:8777/memcore/model-control/profiles \
  -H 'Content-Type: application/json' \
  -d '{
    "profile_name": "FreeLLMAPI memory extraction",
    "provider_type": "openai_compatible",
    "model_name": "memorist-memory-extractor",
    "role": "memory_extraction",
    "endpoint_url": "http://host.docker.internal:31415/v1",
    "endpoint_is_local": true,
    "supports_json_mode": true,
    "supports_structured_output": true,
    "secret_strategy": "env_var",
    "secret_env_var_name": "FREELLMAPI_API_KEY",
    "privacy_acknowledged": true
  }'
```

Then set it as the memory extraction default:

```bash
curl -X POST http://localhost:8777/memcore/model-control/defaults \
  -H 'Content-Type: application/json' \
  -d '{
    "role": "memory_extraction",
    "model_profile_uuid": "PROFILE_UUID_FROM_CREATE_RESPONSE"
  }'
```

Verify what the memory worker will resolve:

```bash
curl 'http://localhost:8777/memcore/model-control/defaults?role=memory_extraction'
```

After this, `/memcore/memory-worker/process-message/{message_uuid}` uses the configured profile and should record `provider_type=openai_compatible` plus a non-null `model_profile_uuid` in `memory_processing_runs`, `prompt_execution_runs`, and `model_usage_events`.

API keys must be supplied through the named environment variable. The API stores only `secret_env_var_name`; raw keys must not be included in `endpoint_url`, profile metadata, cost, quality, latency, or privacy payloads.

## Follow-up: admin UI

There is not yet a dedicated Open WebUI/embedded admin surface for creating memory extraction profiles. Follow-up task: add a Model Control admin panel that can create/test profiles, acknowledge privacy, set `memory_extraction` defaults, and show the resolved Full Mode profile without requiring curl.
