# Release Notes

## Message-first model-led semantic repair (Draft PR #56)

- Ordinary `discard`, `retain_raw_only`, and `manual_review` legacy annotations
  no longer veto whole-message model analysis; hard consent, scope, source and
  privacy boundaries remain local and fail closed.
- Storage schema 26 adds Message summaries, categories, topics, canonical
  concept aliases, entity/process-stage references, exact-span semantic units,
  retrieval plans and explicit semantic job outcomes for SQLite/PostgreSQL.
- Full projects the Message topology into rebuildable FalkorDB. Lite retrieval
  can use scoped Message evidence when no canonical memory claim exists; Full
  message-evidence query integration remains incomplete in this Draft.
- Preflight defaults to 60 seconds; background processing roles default to 120
  seconds with env overrides. Processing input/context settings default to
  100,000 tokens, but provider-side budget enforcement remains incomplete.
- Assistant completion capture now creates immutable initial versions and
  rejects provider-response identity reuse with different content/attachment.

## v0.2.0-beta.3 — WP02 semantic candidate authority (current development baseline)

- One shared Lite/Full semantic orchestration service performs whole-message
  model analysis; persisted route/gate rows are compatibility annotations.
- The ordered memory-extraction certification bundle binds Jakobson v3 and
  semantic candidate analysis v1.
- Deterministic material coverage, UUIDv5 proposal identity, and replay-safe
  SQLite/PostgreSQL proposal/candidate audit persistence are present.
- Storage schema is `26`: SQLite migration `0038` and PostgreSQL migration
  `0025`.
- See `docs/reference/core-memory-processing-walkthrough.md` for the exact
  prompt/response sequence.

The fenced processing-runtime closure below remains part of this beta line.

This candidate closes the processing-node failure boundaries found during the
independent audit of PR #49:

- **Lossless stage replay** — validated structured outputs are stored as
  canonical I-JSON with hashes, corrupt or legacy audit rows are repaired
  instead of replayed as abstentions, and concurrent writers converge on one
  authoritative result.
- **Strict worker fencing** — memory jobs now use per-claim lease tokens,
  monotonic generations, explicit expiry, and short post-provider
  transactions that revalidate job, source, and effective-profile identity
  before candidate, memory, embedding, graph, outbox, or terminal-state
  writes.
- **Embedding recovery** — disabled embedding is a terminal audited state;
  missing projections bypass stage replay, increment durable attempts, and
  regenerate exactly one model/dimension-matched vector.
- **Functional block compaction** — configured providers return typed,
  provenance-exact proposals whose accepted content changes the published
  Active Memory Block. Unsupported claims, omitted constraints, flattened
  conflicts, scope drift, source drift, and lease loss cannot publish.
  Deterministic fallback and preservation of the previous valid block remain
  explicit in diagnostics.
- **Schema and release identity** — new additive SQLite `0030`/`0031` and
  PostgreSQL `0017`/`0018` migrations; public schema `21`; package identity
  `0.2.0-beta.3`.

## v0.2.0-beta.2 — PR5-G integrated product candidate

The first release candidate in which the packaged Open WebUI application
itself exposes the Memorist product:

- **Derivative Open WebUI image** — pinned upstream v0.9.6 (base image by
  digest, frontend rebuilt from the hash-verified source snapshot) with the
  Memorist settings pages, composer Memory On/Off control, and truthful
  "Memory used" disclosure compiled into the production bundle
  (`release/openwebui-image/`).
- **Authenticated proxy reachable** — `/api/v1/memorist/*` routes are
  deterministically ordered ahead of the SPA catch-all with a fail-closed
  startup assertion; known paths answer 401/403/200, never 404.
- **Automatic chat integration** — the managed Memorist chat filter is
  provisioned into Open WebUI's Functions table at every startup; no manual
  Filter or Function installation exists in the product.
- **Truthful packaging** — `package-manifest.ijson` is generated last over
  the final tree with a documented integrity layering, plus an extraction
  validator, upgrade-compatibility contract (stable volumes, reusable
  `.env`), and installed-mode authority in Start/Restart.
- **Real product gates** — pytest against the imported Open WebUI
  application, production-frontend build with bundle inspection, executable
  installer behavioral tests, and Playwright E2E (capture → retrieval →
  disclosure → Memory Off → restart persistence → regeneration) against the
  extracted final ZIP.
- **Processing-node runtime orchestration** — all Memorist processing roles
  resolve project/workspace/global defaults through one audited resolver,
  invoke their effective provider through a shared stage boundary, expose
  truthful multi-level health and stage diagnostics, and fall back locally
  without blocking the main chat path.
- **Cross-session structured project memory** — substantial user-authored and
  assistant-produced project artifacts preserve exact evidence and provenance,
  pass privacy/high-confidence gates, and can be recalled in a later session
  without mislabelling assistant output as a user fact.
- **Embedding runtime wiring** — configured embedding profiles generate and
  consume provider vectors through durable outbox/projection paths while
  deterministic retrieval remains available when embeddings are unavailable.

## v0.2.0 — early public alpha

Memorist's first public-ready candidate: a local-first memory layer for
Open WebUI with a transparent, inspectable memory pipeline.

Highlights:

- **Canonical semantic authority** — one gate/route/candidate/provenance
  pipeline shared by Lite and Full, with gate-before-candidate semantics,
  trust/provenance classification, and no ordinary memory from
  phatic/greeting, privacy, forget, or manual-review turns.
- **Transparent memory attachments** — a read-only, redacted "Memory used"
  panel shows exactly what context was attached to a turn.
- **Truthful per-chat Memory On / Memory Off** — enforced server-side as a
  consent ceiling; regeneration honors the original turn's state.
- **First-run memory node configuration** — admin-only Memory Setup wizard
  with provider-neutral OpenAI-compatible profiles, real role-capability
  tests, and env-var secret references (no plaintext keys in browser or DB).
- **Windows-first one-click local installer** — Docker Desktop-backed setup
  wizard, local `.env` generation with strong secrets, optional local API-key
  capture, health checks, browser launch, and guarded
  start/stop/reset/uninstall scripts.
- **Memory engine baseline** — sentence-level Jakobson analysis, versioned
  evidence-linked memories, scoped rank-fused retrieval with abstention,
  bounded attachments, import (ChatGPT/Claude/Gemini/Open WebUI), Heritage
  export/restore, and a residue-checked forget workflow.

Honest status:

- Lite mode (SQLite, fully local) is the validated local path.
- Full mode (PostgreSQL + FalkorDB) passed all 11 backend/runtime gates in the
  tested Linux Docker environment with no failures or skips.
- The PR5-F candidate packages a real self-contained Full stack, persists the
  installed mode, generates private PostgreSQL credentials, and verifies live
  runtime fields before success.
- Real Windows desktop one-click and lifecycle E2E remains pending and must be
  completed before claiming Windows certification.
- Backend certification is environment-specific; it is not a
  production-readiness or security-audit claim.
- Alpha software: no security audit; schema migrations between alpha versions
  may require export/import; see README "Known limitations".

Storage schema version: 22. Pinned Open WebUI base:
`ghcr.io/open-webui/open-webui:v0.9.6`.

Generated packages under `release/source/` and `release/rc/` are reproducible
build outputs; rebuild them before publishing.
# PR #51 runtime closure

- Added schema 24 provider-attempt audit with SQLite/PostgreSQL parity.
- Fenced initial and repair calls against lease, source, profile, role, and
  contract changes; unknown completions are never blindly repeated.
- Added role-manifest-based certification and a live Jakobson v3 certification
  probe for memory extraction.
- Standardized processing-stage statuses and exposed secret-free attempt traces.
