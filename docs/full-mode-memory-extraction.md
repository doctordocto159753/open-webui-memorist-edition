# Full Mode memory extraction

Full Mode keeps PostgreSQL as the canonical store. FalkorDB is projection-only and receives changes from `graph_projection_outbox`.

## Capture pipeline

The Open WebUI capture adapter writes sessions and messages into PostgreSQL. Capture stops at durable message storage; it does not imply that memory extraction has run.

## Deterministic smoke extraction

The old PostgreSQL smoke helper is retained only as an explicit helper named `_pg_process_message_smoke`. Its rows use `full-smoke-v1` and `deterministic-full-smoke` and should not be used as the production `/memcore/memory-worker/process-message/{message_uuid}` path.

## API-backed structured extraction

The production Full/Postgres process-message route now uses `memory-intelligence-v2` and `memorist-memory-worker-prompt-pack-v2`. It resolves the `memory_extraction` model-control default, calls an OpenAI-compatible provider for profiles with `provider_type=openai_compatible` or `openai_compatible_llm`, validates structured JSON through the Prompt Pack v2 Jakobson schema, and records `prompt_execution_runs` plus `model_usage_events`.

A local OpenAI-compatible endpoint can be configured with a model profile using a `/v1` endpoint URL like:

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

In Full Mode, Model Control writes profiles and defaults to PostgreSQL, the same canonical tables read by `PostgresMemoryWorkerPipeline`. The Processing Nodes admin UI is the primary setup path for API-backed extraction; manual SQL inserts are not required.

### Primary setup path: admin UI

Open the Memorist admin settings in Open WebUI:

```text
Settings → Memorist → Processing Nodes
```

Create a processing node for the provider that should run `memory_extraction`. For an OpenAI-compatible node, fill in these required fields:

- **Node name**: `Local memory extraction`.
- **Provider type**: `OpenAI-compatible`.
- **Base endpoint URL**: the provider `/v1` base URL, such as `http://host.docker.internal:31415/v1` for a local endpoint, or another OpenAI-compatible `/v1` base URL.
- **Model name**: the provider model identifier to call for memory extraction.
- **Endpoint locality**: mark whether the endpoint is local or remote.
- **Secret strategy**: select environment-variable secret storage.
- **Secret environment variable name**: `MEMORIST_PROCESSING_API_KEY`; do not paste raw API keys into profile fields.
- **Capabilities**: enable JSON mode and structured output when the provider supports them.

### Optional OpenAI-compatible endpoint examples

The endpoint can be any provider that implements the OpenAI-compatible API shape required by Memorist. FreeLLMAPI is only one possible OpenAI-compatible endpoint; if you use it locally, configure the same generic fields above with a FreeLLMAPI `/v1` base URL such as `http://host.docker.internal:31415/v1` and keep the API key in `MEMORIST_PROCESSING_API_KEY`.

Other local gateways, self-hosted runtimes, or remote OpenAI-compatible services can use their own `/v1` base URLs as long as they support the configured model and structured-response capabilities.

### Create, edit, and test a profile

1. Select **Create processing node** from **Settings → Memorist → Processing Nodes**.
2. Enter the OpenAI-compatible fields above and save the node as a Model Control profile.
3. To change a provider, open the profile from the processing-node list, edit the endpoint, model, locality, secret env-var name, or capability flags, and save again.
4. Use **Test profile** before assigning the profile as a default. The test should verify that Memorist can reach the endpoint, resolve the configured secret environment variable, receive a compatible response, and validate structured JSON when structured output is enabled.
5. Review the resolved profile details after saving. For Full Mode extraction, the worker should resolve this profile for the `memory_extraction` role.

### Acknowledge privacy for remote endpoints

Local endpoints can be used without remote-provider acknowledgement. For any endpoint marked remote, the UI must show the Model Control privacy disclosure before the profile can become a role default. Review the role-specific data that may be sent, acknowledge the remote risk level, and confirm that `memory_extraction` may send captured user/assistant text or sentence units to that endpoint.

Acknowledgement records the profile, risk level, and data categories in Model Control. It does not store raw API keys, and it does not change the rule that secrets must come from environment variables.

### Assign role defaults

After the profile is saved, tested, and privacy-acknowledged when required:

1. Open **Settings → Memorist → Processing Nodes → Role defaults**.
2. Choose the role, usually `memory_extraction` for Full Mode memory extraction.
3. Select the tested processing node profile.
4. Save the default assignment.
5. Confirm the resolved default shown by the UI. After this, `/memcore/memory-worker/process-message/{message_uuid}` uses the configured profile and should record `provider_type=openai_compatible` plus a non-null `model_profile_uuid` in `memory_processing_runs`, `prompt_execution_runs`, and `model_usage_events`.

API keys must be supplied through the named environment variable. Model Control stores only `secret_env_var_name`; raw keys must not be included in endpoint URLs, profile metadata, cost, quality, latency, privacy payloads, or diagnostics.

### Developer-only curl fallback, non-primary

Use curl only for development, automation, or troubleshooting when the admin UI is unavailable. The UI path above is the primary setup path for operators.

Create an OpenAI-compatible profile:

```bash
curl -X POST http://localhost:8777/memcore/model-control/profiles \
  -H 'Content-Type: application/json' \
  -d '{
    "profile_name": "Local memory extraction",
    "provider_type": "openai_compatible",
    "model_name": "memorist-memory-extractor",
    "role": "memory_extraction",
    "endpoint_url": "http://host.docker.internal:31415/v1",
    "endpoint_is_local": true,
    "supports_json_mode": true,
    "supports_structured_output": true,
    "secret_strategy": "env_var",
    "secret_env_var_name": "MEMORIST_PROCESSING_API_KEY",
    "privacy_acknowledged": true
  }'
```

Set it as the memory extraction default:

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
