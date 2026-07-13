# Memorist memory-control contract

## Authenticated Open WebUI boundary

Browser code calls only `/api/v1/memorist/*`. Open WebUI must mount
`open-webui-integration/memorist/backend/router.py` behind its authenticated session
middleware and populate `request.state.memorist_actor` from the server-verified user and
workspace membership. The router ignores browser identity headers and JSON identity fields,
then signs a short-lived, purpose-bound Core assertion. Signing secrets exist only in the
Open WebUI backend/Filter environment. Direct `/memcore/*` access remains authenticated and
Core host ports are loopback-bound.

The shipped Full and release Compose profiles perform this mount through
`memorist.backend.openwebui_entrypoint`. It reuses Open WebUI v0.9.6's native
`get_verified_user` dependency and maps verified users into the server-configured
`MEMORIST_OPENWEBUI_WORKSPACE_UUID`; the browser cannot select that workspace. The same
entrypoint exposes the scoped status proxy and the review-prepare endpoint.

For review-capable clients, `/memory-control/review/prepare` captures the original message
idempotently and prepares the attachment before model dispatch. The client previews and
approves that UUID, then resubmits the same Open WebUI message ID with
`memorist_review_ui_active=true` and the exact `memorist_approved_attachment_uuid`. The
Filter observes a duplicate capture of the original input, verifies the approved generation,
records delivery, and injects only the server-rendered attachment. Without interception the
Filter cancels before send and captures the assistant without attachment attribution.

## Trusted request and network boundary

Sensitive Memory Control, retrieval, attachment, preflight, capture, and assistant-completion
routes accept only a short-lived HMAC actor assertion plus the internal service credential.
The assertion binds user UUID, workspace UUID, issuer, audience, request method/path, issue and
expiry times, and a single-use nonce. Browser JavaScript has neither signing material nor a
supported raw-identity-header path; it calls an authenticated Open WebUI backend endpoint.

Production deployments expose the Core host port on loopback only. Open WebUI uses the Compose
network address `http://memorist-core:8777`; localhost is not treated as an authentication
boundary. `MEMORIST_ACTOR_ASSERTION_SECRET` and `MEMORIST_ACTOR_SERVICE_TOKEN` are mandatory in
production and must be distinct high-entropy secrets.

## Certified graph runtime

PR4-B certifies `falkordb/falkordb:v4.18.10`. CI and the Full Compose profile use that exact tag.
When the graph backend is disabled or its URL is absent, Full records respectively
`graph_backend_disabled` or `falkordb_url_missing` without opening a socket. PostgreSQL remains
canonical in all graph states.

Memorist applies one normalized, immutable turn policy in both runtime profiles. The
runtime profile is server-controlled and is never accepted from request metadata.

| Turn policy | Capture user | Recall | Build/deliver attachment | Capture assistant | Queue extraction |
| --- | --- | --- | --- | --- | --- |
| `full` | yes | yes | yes | yes | yes |
| `no_recall` | yes | no | no | yes | yes |
| `private` | no | no | no | no | no |

`Full Memory` is a turn policy. `Full runtime` is the PostgreSQL + FalkorDB deployment
profile. Internally these are represented independently as `turn_policy=full` and
`runtime_profile=full|lite`.

## Request and precedence

Clients may send only the following control object:

```json
{
  "memorist": {
    "turn_policy": "full",
    "attachment_review": false
  }
}
```

Policy precedence is `turn → chat → user → system`. Defaults have equivalent persistence
semantics in SQLite and PostgreSQL. A request containing `runtime_profile`, canonical-store
selection, rendered attachment content, or graph sources is rejected.

Memorist controls do not alter Open WebUI native memory. Interfaces must label the two
controls separately as **Memorist Recall** and **Open WebUI Native Memory**.

## Runtime storage

The memory-control service selects exactly one canonical connection:

- Lite runtime uses SQLite repositories.
- Full runtime uses PostgreSQL repositories. PostgreSQL stores turn contracts, retrieval
  plans, attachments, sources, lifecycle events, delivery audit, and regeneration records.
- FalkorDB is a projection and retrieval aid in Full runtime; it is never canonical.

Full retrieval validates every graph result against the PostgreSQL memory version and
workspace scope before it can enter an attachment. A FalkorDB outage produces
`graph_status=degraded` with a persisted `degraded_reason`; retrieval continues from scoped
PostgreSQL candidates and active blocks. It never opens or writes a SQLite fallback.

## Attachment lifecycle and APIs

The lifecycle is persisted as `prepared`, `approved`, `delivered`, `suppressed`,
`cancelled_before_send`, `user_rejected`, and `used_for_response`. State transitions are
atomic and idempotent. Delivery is rejected for expired, wrong-owner, wrong-workspace,
wrong-input, non-delivered, or terminal-state attachments.

`Send without Memorist` after a prepared preview remains a Full-policy turn with a
`suppressed` or `cancelled_before_send` attachment. Recall already occurred to render the
preview, so this path is not mislabeled as `no_recall`. The Filter reuses the original
capture, performs no second retrieval, injects no context, and captures the assistant with
`attachment_uuid=null`.

Runtime-aware endpoints under `/memcore/memory-control` provide policy resolution/defaults
and attachment preview, source fetch, approval, suppression, cancellation, delivery,
rejection, and regeneration without recall. Attachment endpoints require the trusted
`X-Memorist-User-Id` identity and matching `X-Memorist-Workspace-Id` scope.

Regeneration returns the original prompt, fixes the regenerated turn to `no_recall`, creates
no attachment or duplicate user capture, captures the regenerated assistant response, and
keeps the original delivered-attachment audit unchanged.

## Private guarantee

Policy resolution occurs before session resolution. A Private turn returns before any
session, message, job, retrieval run, graph query, attachment, content-bearing usage event,
or assistant capture is created. In Full runtime this also means no PostgreSQL or FalkorDB
turn-content trace.

## Certification

The PR4-B workflow exposes six release gates: Lite contract, Full PostgreSQL contract, real
graph and degraded behavior, attachment security, regeneration, and type/lint checks. Full
certification jobs fail if any selected PostgreSQL test is skipped.
