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
