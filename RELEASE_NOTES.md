# Release Notes

## v0.2.0 — early public alpha (current)

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

Storage schema version: 18. Pinned Open WebUI base:
`ghcr.io/open-webui/open-webui:v0.9.6`.

Generated packages under `release/source/` and `release/rc/` are reproducible
build outputs; rebuild them before publishing.
