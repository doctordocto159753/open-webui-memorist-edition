# Preflight and Attachment Budgets

Preflight is the send-time path that decides whether Memorist should attach memory to the next Open WebUI request.

```text
current message
-> memorist.preflight_planning@2.1
-> persisted audited retrieval plan
-> local candidate generation
-> canonical Memory and scoped Message Evidence fusion
-> ranking and safety filtering
-> token budget decision
-> Memory Context Attachment
```

## Failure Behavior

The integration is fail-open by default. If preflight times out, the writer queue is under pressure, or attachment creation is not useful for the current model context, Open WebUI receives the original request without memory context.

The managed integration default is 60 seconds. `MEMORIST_PREFLIGHT_TIMEOUT_MS`
can override it. Lite and Full use the same typed planning contract and persist
the accepted plan in `model_retrieval_plans`; Full stores canonical retrieval
and Message Evidence provenance in PostgreSQL and may add rebuildable graph
signals from FalkorDB.

Preflight can return degraded statuses such as:

- `degraded_lite`
- `skipped_backpressure`
- existing fail-open/timeout statuses

## Budget Inputs

Budget calculation uses:

- retrieval mode: `lite`, `standard`, `full`, `debug`;
- requested token budget;
- model context window from request metadata;
- local model registry;
- known built-in model defaults;
- `MEMORIST_UNKNOWN_MODEL_CONTEXT_WINDOW`;
- estimated recent conversation tokens;
- reserved completion tokens;
- safety margin.

Endpoint:

```sh
curl -X POST http://localhost:8777/memcore/budget/attachment \
  -H "Content-Type: application/json" \
  -d '{"target_model":"local-model","recent_conversation_text":"short context"}'
```

## Model Registry

Use the local registry when Open WebUI model names are custom or unknown:

- `GET /memcore/model-registry`
- `POST /memcore/model-registry`
- `PATCH /memcore/model-registry/{model_profile_uuid}`

Example:

```json
{
  "provider": "local",
  "model_name": "qwen-local",
  "model_aliases": ["qwen-local", "qwen2.5-custom"],
  "context_window": 32768,
  "source": "user_configured"
}
```

The registry is local metadata only. Provider credentials stay in Open WebUI.
