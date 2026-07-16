# Memorist — Open WebUI Memorist Edition

Package version: `0.2.0-beta.1`<br>
Storage schema version: `18`

**Remember with consent, recall with provenance.**

Memorist is a local-first memory layer for [Open WebUI](https://github.com/open-webui/open-webui)
that lets a chat system remember with consent, show what it remembers, and
keep the memory machine inspectable.

> **Status: early public alpha.** Lite mode uses SQLite. Full mode uses
> PostgreSQL + FalkorDB and is certified in the tested local Docker environment
> (11/11 external gates; Windows 11 one-click smoke). Expect rough edges; don't expect silent data loss — nothing here
> deletes memory without asking.

## Why memory, and why this way

Long-running work with LLMs loses continuity: projects, preferences,
decisions, corrections, and style constraints evaporate between sessions.
Most fixes summarize old chats into a hidden prompt — invisible, unauditable,
and easy to poison.

Memorist takes the opposite approach: **memory is made visible.** It separates
capture, semantic routing, candidate creation, retrieval, and attachment so
users and developers can inspect how a conversation becomes reusable context.
This is not chat-history search; it is a memory *machine* with parts you can
open:

```text
Open WebUI chat
-> raw evidence capture (unchanged messages)
-> sentence units with exact offsets
-> Jakobson communication analysis
-> canonical route + gate decision        (gate before candidate)
-> route-specific candidate extraction    (evidence-linked, trust-classified)
-> consolidation into versioned memories
-> scoped, budget-aware retrieval
-> bounded Memory Context Attachment      (data, not command)
-> visible "Memory used" panel in chat
```

The user's prompt is never modified. Memory rides alongside as separate,
provenance-tagged, untrusted context — and if the memory engine is down, chat
fails open and just works without it.

## What you can do with it

- **Chat with memory you control.** A per-chat **Memory On / Memory Off**
  switch sits beside the composer and is enforced server-side: an Off turn is
  never captured, processed, retrieved from, or attached to.
- **See exactly what was remembered.** Turns that used memory show a
  read-only, redacted **"Memory used"** panel with provenance — a window into
  what the model actually received.
- **Run fully local, no API key.** Every processing role has a local
  deterministic fallback. Remote OpenAI-compatible providers are optional,
  per-role, and gated behind an explicit privacy acknowledgement.
- **Install without being a developer.** A Windows-first, Docker-backed
  one-click installer sets up services, secrets, and provider keys — no Git,
  Python, or `uv` required.
- **Import, export, and forget.** Provider archives (ChatGPT, Claude, Gemini,
  Open WebUI) import as untrusted historical evidence; Heritage export gives
  you a portable, verifiable package; the forget workflow erases with residue
  checks and receipts.

## Quick start (Windows one-click)

Requirements: Windows 10/11 with **Docker Desktop** installed and running,
~5 GB free disk. (Docker Desktop is currently required; a Dockerless build is
a possible future direction, not this release.)

1. Download and unzip the release package (`memorist-openwebui-<version>.zip`).
2. Double-click **`Memorist.cmd`**.
3. Follow the short wizard — it checks Docker, generates a private `.env`,
   optionally captures a provider API key locally, starts the services, and
   opens <http://localhost:3000>.

Lifecycle scripts ship in the package: `Start-`, `Stop-`, `Restart-`,
`Show-Memorist-Logs`, `Reset-Memorist-Data`, `Uninstall-Memorist`. The same
scripts run under PowerShell 7 on macOS/Linux, and bash equivalents are
included. Full walkthrough: [docs/INSTALLATION.md](docs/INSTALLATION.md).

### Quick start (developers, from source)

```sh
git clone https://github.com/doctordocto159753/open-webui-memorist-edition.git
cd open-webui-memorist-edition
cp .env.example .env
docker compose -f docker-compose.lite.yml up --build
curl http://localhost:8777/memcore/health
```

Pure-Python development without Docker works too — see
[docs/DEVELOPMENT.md](docs/DEVELOPMENT.md).

## Lite vs Full

| | **Lite** (default) | **Full** |
| --- | --- | --- |
| Canonical store | SQLite | PostgreSQL |
| Graph projection | disabled | FalkorDB (rebuildable, never canonical) |
| Embeddings | optional | optional |
| Footprint | low — runs on modest machines | heavier |
| Status | validated local path | certified in tested local Docker environment |

Lite and Full share one canonical semantic pipeline, so both make the same
memory decisions — Full adds storage scale and graph projection, not different
semantics.

## The memory machine, briefly

Raw messages are **evidence**, not memory. Deterministic sentence units feed a
Jakobson six-factor communication analysis that distinguishes an instruction
to the AI from a team obligation, a process fact, a terminology rule, or an
emotional stance. A canonical authority routes each signal and **gates before
any candidate exists** — greetings and small talk create no memory, and
privacy/forget/manual-review requests never become ordinary memories.
Candidates carry evidence links and a trust/provenance classification
(assistant/tool/system text never silently gains user authority), then
consolidate into versioned memories where corrections create new versions
instead of rewriting history.

Retrieval is scoped, rank-fused, and explainable, with explicit abstention
when evidence is weak. Attachments are bounded to the model's context budget,
delimiter-escaped, and marked as data — not instruction.

Read the full walk-through: [docs/MEMORY_MACHINE.md](docs/MEMORY_MACHINE.md).

## Configuring memory processing nodes

Memorist uses explicit **Model Control Plane** roles instead of one global
model: `main_chat_observed` (metadata only — Open WebUI keeps owning the chat
model), `preflight`, `memory_extraction`, `embedding`, `privacy_sensitivity`,
`block_compaction`, and `import_reconstruction`.

First-run setup lives at **Settings → Memorist → Memory Setup** (admin-only).
Choose local deterministic (no key), or point a role at any OpenAI-compatible
endpoint. Secrets follow one rule everywhere: **the browser and database store
only an environment-variable name; the value lives in your local `.env` /
container environment**, written once by the installer and never echoed,
logged, or returned by any API. Remote endpoints require an explicit privacy
acknowledgement before they become role defaults.

## Privacy and security model

- Local-first by default; `MEMORIST_LOCAL_ONLY=false` is rejected; no
  telemetry.
- Memory Off is a server-side consent ceiling, not a UI hint.
- Memory and imported text are treated as untrusted data — escaped, flagged,
  and never promoted to directives; prompt injection is mitigated, not
  eliminated.
- The trust boundary is your machine: `.env` is plaintext on your disk by
  documented design, and there is no encryption at rest.
- Any remote provider you configure sees role-specific data — that's your
  call, made explicit.

Details, caveats, and vulnerability reporting: [SECURITY.md](SECURITY.md).

## Screenshots

*(Screenshots of the Memory used panel, the Memory On/Off toggle, and the
Memory Setup wizard are planned for the next release cut. Until then, the
installer gets you to a live instance in a few minutes.)*

## Documentation

| Doc | What it covers |
| --- | --- |
| [docs/INSTALLATION.md](docs/INSTALLATION.md) | Windows one-click install, Docker Desktop, Lite/Full, `.env` and API keys, lifecycle scripts, backup/upgrade |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | components, runtimes, storage split, recall path, Model Control Plane, installer/release architecture, CI |
| [docs/MEMORY_MACHINE.md](docs/MEMORY_MACHINE.md) | capture → analysis → gate → route → candidate → consolidation → retrieval → attachment → display, and the consent boundary |
| [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) | dev setup, tests, workflows, packaging, release checklist |
| [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | Docker/WSL2, ports, provider tests, memory behavior questions, reset/recovery |
| [SECURITY.md](SECURITY.md) | trust model, secrets, remote-provider privacy, what is not guaranteed |
| [docs/reference/](docs/reference/) | deep dives: memory engine essay, storage runtimes, import/heritage/forget, prompts, model control |
| [docs/fa/](docs/fa/) | Persian-language architecture documents |

## Development and contributing

Backend is Python 3.12 / FastAPI managed with `uv`; frontend components are
TypeScript tested with vitest; CI enforces the semantic, consent, attachment,
secret-handling, and installer contracts on every PR (self-hosted runners —
forks may need to adapt CI). Start with
[docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) and
[CONTRIBUTING.md](CONTRIBUTING.md).

## Known limitations

- Alpha software: no security audit, no stability guarantee, schema
  migrations between alpha versions may require export/import.
- Docker Desktop (or Docker Engine + Compose) is required for the release
  path; there is no signed native installer yet.
- Full mode's external certification is incomplete — treat it as a preview.
- Semantic quality depends on the models you configure; the local
  deterministic fallback is safe but intentionally conservative.
- Provider export formats change; import adapters are defensive, not
  guaranteed.
- CI validates installer dry-run and compose config on Linux runners, not
  every Windows desktop scenario.

## License and attribution

Licensed under the [MIT License](LICENSE). Memorist is an independent,
community project that runs beside Open WebUI — it is not an official
Open WebUI product. Open WebUI remains the parent chat UI and is developed by
its own community under its own license.
