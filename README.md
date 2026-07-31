# Memorist — Open WebUI Memorist Edition

Package version: `0.2.0-beta.3`

Storage schema version: `27`

Current development line: **WP02 semantic candidate authority**.

**Memorist is an independent community project built on Open WebUI. It is not
affiliated with or endorsed by the Open WebUI team.**

<img width="1280" height="640" alt="Memorist for Open WebUI" src="https://github.com/user-attachments/assets/484864e3-fac7-4a78-a44e-7a82c7495dd2" />

**Remember with consent, recall with provenance.**

Memorist is a local-first memory layer for
[Open WebUI](https://github.com/open-webui/open-webui). It captures
conversation evidence with consent, turns eligible material into versioned
memories, retrieves only scoped context, and shows what was delivered to the
chat model.

> **Status: beta development candidate.** Lite uses SQLite. Full uses
> PostgreSQL with optional FalkorDB projection. Consolidated CI covers quality,
> PostgreSQL/Full/FalkorDB, package lifecycle, and one-deployment Product E2E
> on GitHub-hosted runners. Native Windows desktop certification remains a
> separate release check.

## Why this memory architecture?

Appending old chats to a hidden prompt is difficult to audit and easy to
poison. Memorist separates evidence, interpretation, persistence, and recall:

```text
Open WebUI chat
-> immutable user/assistant capture
-> TextEnvelope and exact text units
-> structural envelope and exact spans
-> whole-message model-led semantic analysis v1
-> Message summary/categories/topics/concepts/process-stage ledger
-> Jakobson/route/gate compatibility annotations (non-vetoing)
-> strict schema and exact-evidence validation
-> deterministic coverage and proposal identity
-> replay-safe candidate persistence
-> consolidation into versioned memories
-> scoped canonical-memory + Message-evidence retrieval
-> separate Memory Context Attachment       (untrusted data, not command)
-> read-only "Memory used" display
```

The original user prompt is not rewritten. Retrieved memory is delivered as a
separate, provenance-tagged context message. If recall is unavailable, chat
fails open without an attachment.

## What you can do

- **Control consent per chat.** Memory Off is enforced server-side: no capture,
  processing, retrieval, or attachment occurs for that turn.
- **Inspect delivered memory.** The read-only "Memory used" view is built from
  canonical delivery records and redacted provenance.
- **Run without an API key.** Processing roles have conservative local
  deterministic fallbacks. Remote OpenAI-compatible profiles are optional and
  require explicit privacy acknowledgement.
- **Use one semantic decision path in Lite and Full.** Both runtimes call the
  same `SemanticCandidatePlanningService`; storage adapters differ, semantic
  authority does not.
- **Retrieve meaningful messages before promotion.** Model-generated query
  understanding drives a scope-checked topic/concept/process-stage traversal;
  a missing canonical claim no longer makes project evidence invisible.
- **Import, export, and forget.** Imports remain untrusted historical evidence,
  Heritage packages are verifiable, and forget performs dependency traversal,
  residue checking, and receipt creation.

## Quick start

### Windows release package

Requirements: Windows 10/11, Docker Desktop running, and about 5 GB free disk.

1. Download and unzip `memorist-openwebui-<version>.zip`.
2. Double-click `Memorist.cmd`.
3. Follow the wizard. It checks Docker and ports, creates a private `.env`,
   validates Compose, starts the services, and opens
   <http://localhost:3000>.

The package includes start, stop, restart, logs, reset, and uninstall scripts.
See [Installation](docs/INSTALLATION.md) before reset or upgrade operations.

### Developers

```sh
git clone https://github.com/doctordocto159753/open-webui-memorist-edition.git
cd open-webui-memorist-edition
cp .env.example .env
docker compose -f docker-compose.lite.yml up --build
curl http://localhost:8777/memcore/health
```

Pure-Python development is documented in
[Development](docs/DEVELOPMENT.md).

## Lite and Full

| | Lite (default) | Full |
| --- | --- | --- |
| Canonical store | SQLite | PostgreSQL |
| Write discipline | serialized SQLite write actor | PostgreSQL transactions, locks, durable jobs/outboxes |
| Semantic orchestration | shared WP02 service | the same shared WP02 service |
| Graph | not required | optional FalkorDB projection |
| Embeddings | optional and rebuildable | optional and rebuildable |
| Footprint | lower | higher |

SQLite/PostgreSQL records are authoritative. FTS, embeddings, active blocks,
attachments, and FalkorDB are derived or delivery artifacts and must never
become semantic authority.

## One chat turn, accurately

Before the main model runs, the server-side Filter resolves turn policy,
captures the user message, performs scoped preflight retrieval, and may insert
a separate `memorist_context` attachment. Open WebUI then runs its selected
chat model. The Filter outlet captures the assistant response and links it to
the input and delivered attachment.

Each captured message is processed asynchronously:

1. exact text units and Jakobson v3 annotations are recorded;
2. canonical route and gate decisions are persisted;
3. only eligible gates enter bounded whole-message semantic analysis;
4. deterministic code validates evidence, plans complete coverage, and creates
   at most one proposal per durable unit;
5. proposal UUIDs become replay-safe candidate UUIDs;
6. consolidation creates, reinforces, supersedes, rejects, or flags canonical
   memory versions;
7. later preflight runs retrieve those versions for future turns.

The detailed source-level example is
[Walkthrough پردازش حافظه در موتور مرکزی](docs/reference/core-memory-processing-walkthrough.md).
The compact conceptual guide is [The Memory Machine](docs/MEMORY_MACHINE.md).

## Processing nodes and model roles

The Model Control Plane resolves explicit roles instead of using one global
model:

- `main_chat_observed` — metadata only; Open WebUI owns the main chat model;
- `preflight` — bounded retrieval-planning assistance;
- `memory_extraction` — the certified Jakobson v3 and semantic candidate v1
  bundle;
- `high_confidence_extraction`, `privacy_sensitivity`,
  `block_compaction`, `import_reconstruction`, and `embedding` — specialized
  post-capture or projection work.

Resolution is project → workspace → global → documented inheritance →
built-in fallback. Remote profiles reference an environment-variable **name**;
secret values remain in the local environment and are not stored in the
database or returned to the browser. A profile edit makes its certification
stale until the exact role contract bundle passes again.

See [Model Control Plane](docs/reference/model-control-plane.md).

## Security boundaries

- Memory Off is a server-side consent ceiling.
- Current and historical text is untrusted data, including
  `memorist_context`.
- Gate, route, privacy, provenance, coverage disposition, and proposal
  identity are deterministic authorities; a model cannot choose them.
- Bounded semantic context is limited to the same user, session, workspace,
  and project. Hidden, deleted, redacted, system, tool, sensitive, and
  cross-boundary records are excluded.
- Assistant content remains `assistant_claim` unless a current user uniquely
  references and explicitly ratifies or corrects it.
- Remote providers see the role-specific payload sent to them. There is no
  encryption at rest in this release.

Read [Security and Privacy](SECURITY.md) for threat-model limits.

## Documentation

| Document | Scope |
| --- | --- |
| [Walkthrough پردازش حافظه در موتور مرکزی](docs/reference/core-memory-processing-walkthrough.md) | one prompt and one model response through capture, recall, semantic processing, persistence, replay, consolidation, and later retrieval |
| [Architecture](docs/ARCHITECTURE.md) | component and authority boundaries, Lite/Full storage, runtime paths, CI |
| [The Memory Machine](docs/MEMORY_MACHINE.md) | compact lifecycle and consent model |
| [Semantic candidate authority](docs/reference/semantic-candidate-authority.md) | frozen WP02 contracts, field authority, ordering, identity |
| [Installation](docs/INSTALLATION.md) | package layout, Docker, configuration, lifecycle, backup/upgrade |
| [Model Control Plane](docs/reference/model-control-plane.md) | roles, profiles, certification, effective runtime resolution |
| [Development](docs/DEVELOPMENT.md) | setup, tests, workflow, packaging |
| [Troubleshooting](docs/TROUBLESHOOTING.md) | startup, provider, processing, storage, recovery |
| [Reference index](docs/reference/README.md) | current deep-dive documentation |
| [Persian architecture](docs/fa/) | Persian long-form architecture material |

Runtime prompt Markdown under `memorist-core/src/.../prompts/` is executable
contract content, not general documentation. Files under `docs/historical/`
describe prior baselines and are not current runtime authority.

## Development and contributing

The backend uses Python 3.12/FastAPI with `uv`; UI components use TypeScript
and vitest. The authoritative workflow is
`.github/workflows/ci-consolidated.yml`, with four jobs covering quality and
integration, PostgreSQL/Full/FalkorDB, package lifecycle, and Product E2E.

Start with [Development](docs/DEVELOPMENT.md) and
[Contributing](CONTRIBUTING.md).

## Known limitations

- `0.2.0-beta.3` is a beta development candidate, not a stability or security
  guarantee; no independent security audit is claimed.
- Docker Desktop (or Docker Engine with Compose) is required for the packaged
  path; there is no signed native installer.
- CI exercises the package and Product E2E on GitHub-hosted runners, not every
  Windows desktop configuration.
- Semantic quality depends on configured providers. Deterministic fallbacks
  are safe and deliberately conservative.
- Provider archive formats change; import adapters are defensive, not
  guaranteed.

## License and attribution

Licensed under the [MIT License](LICENSE). Memorist runs beside Open WebUI and
is not an official Open WebUI product.
