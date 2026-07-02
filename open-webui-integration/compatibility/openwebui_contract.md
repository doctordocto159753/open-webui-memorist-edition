# Open WebUI Filter Compatibility Contract

Memorist v0.2.0-beta.1 targets the server-side Open WebUI Filter lifecycle. The pinned local container-smoke target is `ghcr.io/open-webui/open-webui:v0.9.6`. Automated release gates verify the Memorist parser, fail-open behavior, inlet/outlet idempotency, payload fixtures, and Model Control Plane; the optional container smoke verifies that the pinned Open WebUI image and Memorist Core can start together locally.

This is not a claim that every future Open WebUI payload variant is certified. Open WebUI remains the parent chat product; Memorist adds local memory capture and bounded memory context through trusted server-side integration files.

## Entrypoints

- `inlet(body, __user__)`: resolves or creates a Memorist session, captures the current user message, optionally runs preflight retrieval, and inserts a separate memory context message.
- `outlet(body, __user__)`: captures the assistant response once per provider response/content hash.
- `Valves`: controls local Memorist Core URL, enabled/fail-open behavior, preflight timeout, retrieval mode, and maximum attachment token cap.

## Required and Optional Fields

Required for full behavior:

- `messages`: list of chat messages with at least one `{"role": "user", "content": ...}` item for `inlet`.

Optional but preferred:

- `conversation_id` or `chat_id`: stable Open WebUI chat identifier.
- `temporary_chat_id` or `temp_chat_id`: early lifecycle temporary chat identifier.
- `metadata.client_session_nonce`: local browser/session nonce when available.
- `messages[*].id` or `metadata.message_id`: source message identifier.
- `model`, `metadata.model`, `model_context_window`: selected model metadata for dynamic attachment budget.
- `created_at` or `timestamp`: timestamp bucket for idempotency fallback.
- `__user__.id` or `__user__.email`: local user identifier.

## Fallbacks

- Missing stable chat ID falls back to `client_session_nonce`, temporary chat ID, then first-message fingerprint.
- Missing user ID is allowed, but aliases become less specific and are not merged across known users.
- Missing model metadata uses Memorist Core's conservative fallback context window and records a warning.
- Unsupported body shape fails open and leaves the user prompt unchanged.

## Attachment Rules

- The original user message is restored byte-for-byte after `inlet`.
- Memory context is inserted as a separate `system`-role message named `memorist_context` when `messages` is a list.
- Inserted content is labeled as Memorist memory data and untrusted data, not parent policy.
- Closing delimiters inside memory text are escaped.
- If `messages` is not list-like, the filter records `memorist_attachment_warning` in metadata and does not mutate user text.

## Assistant Capture

- `outlet` uses provider response ID when present and falls back to assistant content hash.
- Duplicate outlet callbacks are ignored in-process and de-duplicated again by Memorist Core.

## Known Incompatibilities

- Client-only Open WebUI customizations that do not execute server-side Filters are not supported.
- Payloads that omit both messages and assistant response fields cannot be captured.
- This integration is local-only and rejects remote Memorist Core URLs.
- The optional container smoke does not automatically install the Filter into an Open WebUI account; that step remains manual for this beta candidate.
