# Memory processing node setup

Open **Settings → Memorist → Memory Setup** after first launch. The page shows
whether memory processing is ready and which roles use an explicit profile or a
built-in local fallback.

## Roles

- **Memory extraction** processes captured user and assistant messages after a
  response. It is the primary configurable role.
- **High-confidence extraction** is an optional stricter pass. Without an
  explicit profile it inherits the safe local extraction fallback.
- **Embedding** is optional in Lite mode. Configure it only when vector retrieval
  is enabled; changing embedding profiles can require re-indexing.
- **Privacy/sensitivity** and **import reconstruction** are optional roles backed
  by the existing Model Control contract.
- The **main chat model** remains selected in Open WebUI. Memorist observes its
  metadata and does not configure it as a processing node.

## Local deterministic or OpenAI-compatible

**Local deterministic** needs no provider account or API key. It keeps processing
local, is the safe Lite/Full extraction fallback, and may be lower quality than a
capable structured-output model.

**OpenAI-compatible / custom endpoint** accepts a provider label, endpoint base
URL, model ID, capability flags, and an environment-variable reference. The
contract is provider-neutral; FreeLLMAPI, OpenAI, a local gateway, or another
compatible service are examples, not hard-coded product dependencies.

Connection testing performs the existing role-aware lightweight call:

- extraction roles use `POST /v1/chat/completions`;
- embedding roles use `POST /v1/embeddings`;
- JSON/structured-output flags are tested when selected.

Failures are bounded and sanitized before they reach the UI.

## API keys and secrets

Memorist currently uses environment references and does not implement weak
database encryption for provider keys.

1. Put the API key in the `memorist-core` process/container environment.
2. In Memory Setup, enter only the variable name, for example
   `MEMORIST_MEMORY_EXTRACTION_API_KEY`.
3. Save and test the profile.

The key value is resolved only inside the backend process. It is not stored in
SQLite/PostgreSQL, returned by profile/status APIs, or retained in the setup DOM.
Profile summaries report only whether a secret reference is configured. Never
paste API keys into issues, screenshots, chat transcripts, logs, endpoint URLs,
or profile metadata.

Remote endpoints require explicit privacy acknowledgement before they can become
a role default because role-specific conversation or memory text may leave the
local machine.

## First-run status and access control

`GET /api/v1/memorist/model-control/setup/status` is exposed through the
authenticated Open WebUI integration. Model setup reads and writes require a
verified Open WebUI administrator; browser-supplied actor/workspace headers are
ignored. Default assignments are forced to the trusted workspace.

A fresh Lite installation normally reports **Ready — local fallback available**
and recommends optional configuration rather than forcing a remote provider.
Full mode also retains deterministic extraction fallback; an embedding provider
is needed only when the deployment enables vector retrieval.

## Memory Off

Provider configuration does not bypass the chat control. **Memory Off** remains
the server-side consent ceiling: that turn creates no capture/processing job and
performs no memory retrieval or attachment, even when remote profiles are fully
configured.

## Troubleshooting

- **Secret environment variable is not set**: add the named variable to the
  backend/container environment and restart that service.
- **Model not found**: use the provider's exact model ID.
- **JSON response format rejected**: disable the capability flag or choose a
  compatible model.
- **Privacy acknowledgement required**: review the remote data boundary and
  acknowledge it before assigning the profile.
- **Connection refused/timeout**: confirm the base URL is reachable from the
  Memorist backend container, not only from the browser.
