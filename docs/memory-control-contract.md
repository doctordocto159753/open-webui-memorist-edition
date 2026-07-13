# Memorist memory-control contract

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
