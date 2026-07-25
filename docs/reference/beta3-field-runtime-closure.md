# Beta.3 field-install and processing-node closure ledger

This ledger is the implementation-time source of truth for the beta.3 field
closure. It records the observed failure, the architectural boundary that owns
the fix, and the proof required before the branch can be considered ready.
Evidence is updated as tests and live certification complete.

## Scope and invariants

- Base: `main` at `f8e2dfdd863eba8141cd7c1e4cea6be522247191`
- Working branch: `codex/beta3-field-install-runtime-closure`
- Runtime version: `0.2.0-beta.3`; storage schema before this closure: `21`,
  after additive closure migrations: `22`
- Existing Docker volumes and local release evidence are preserved.
- The release, tag, merged state, and published beta.3 assets are out of scope.
- Open WebUI talks to Core at the fixed service URL
  `http://memorist-core:8777`; host-published ports are installer state only.
- Secrets are referenced by environment-variable name and are never persisted
  in model-control rows, diagnostics, test output, or logs.

## Failure-to-proof ledger

| ID | Observed evidence on the base commit | Owning boundary and closure | Required proof |
|---|---|---|---|
| F1 | Full capture enters `_pg_enqueue_capture_jobs`, wraps a PostgreSQL connection, and `openwebui/model_scheduling.py` unconditionally constructs the SQLite `ModelControlRepository`. PostgreSQL JSONB values therefore reach the SQLite row decoder. | One canonical model-control repository selector, chosen from runtime storage authority and used by routes plus scheduling/runtime call sites. | Full PostgreSQL capture with a remote default enqueues and completes without row-validation/JSONB errors; Lite remains green. |
| F2 | `Install-Memorist.ps1` starts at ports 3000/8777 and only attempts a loopback bind. Windows excluded TCP ranges are not considered. | A deterministic host-port allocator considers both active listeners and excluded IPv4/IPv6 TCP ranges, then persists the chosen host ports to `.env`. Container Core stays on 8777. | Pester fixtures for occupied and excluded ranges, deterministic next-port selection, persisted env values, and Compose rendering. |
| F3 | A fresh extraction without its old `.env` generates a new PostgreSQL password while the stable Compose project can reuse `memorist_memorist-postgres-data`; `POSTGRES_PASSWORD` does not rotate an initialized cluster. | Installation identity and credential reconciliation precede generation. Existing authoritative state is reused when recoverable; an orphaned database volume fails closed with recovery guidance. A secret-safe DSN preflight runs before the application stack is declared ready. | Fresh install, same-directory upgrade, new-directory upgrade with an existing project, orphan-volume refusal, wrong-DSN refusal, and preserved-data restart. |
| F4 | Compose currently uses `MEMORIST_CORE_URL=http://memorist-core:8777`, but installer-selected host ports are also used for host health/output and lack a regression contract. | Separate `MEMORIST_CORE_HOST_PORT` from the fixed container service URL in code, tests, and documentation. | Compose tests prove every mode keeps the internal URL at 8777 while arbitrary safe host ports publish correctly. |
| F5 | The OpenAI-compatible health probe requests a JSON object but treats an exact marker mismatch as role incompatibility; strict JSON Schema is not used when advertised. | Capability-aware schema requests, exact instructions, one bounded corrective retry for valid-but-wrong JSON, and structured/sanitized diagnostics that distinguish transport, auth, model, schema, and semantic-marker failures. | Controlled providers for exact success, recoverable marker mismatch, persistent semantic mismatch, malformed JSON, unsupported schema mode, auth, missing model, timeout, and rate limit. |
| F6 | The browser requires a successful in-memory test result before setting a default, but backend `set_default` only checks enabled/role/privacy. Refresh loses the browser gate, and profile edits do not stale prior health. | Persist a backend certification fingerprint with health results. Backend default assignment and runtime resolution require a current compatible certification. Relevant profile edits make certification stale. UI renders backend certification and supports explicit default removal/replacement. | API and frontend tests cover refresh, direct API bypass, edit-induced staleness, replacement, removal, and runtime refusal/fallback. |
| F7 | The Open WebUI client applies `preflight_timeout_ms` to health, capture, control-plane, provider-test, import, and admin operations. | Operation-specific timeout classes: chat preflight/capture, control plane, provider test, import, and admin/diagnostic. Proxy timeouts map to 504; safe Core 4xx categories and sanitized detail survive the proxy. | Slow controlled endpoints prove chat remains bounded while provider tests, import, and admin/control calls receive their own budgets. |
| F8 | Fail-open chat only adds transient `memorist_last_error` metadata and a warning; `/openwebui/status` reports Core connected with no durable degraded reason. | Record bounded, sanitized per-stage integration outcomes in canonical storage and expose latest outcome plus last success/failure times in status and admin diagnostics. A later unrelated successful stage cannot hide a capture failure; the flag clears after the failed stage recovers. | Capture, recall, attachment, provider, and fallback failures remain fail-open but become visible and auditable after refresh/restart. |
| F9 | Installer/Compose pass five role secrets; `preflight` and `block_compaction` are absent. Public status conflates a secret strategy with an available reference. | Pass all seven processing-role references and report four distinct states: reference configured, available in Core, authentication validated, and certification current/stale. | Compose/config matrix and setup-status tests cover every role with no secret value disclosure. |
| F10 | Base Compose defaults `ENABLE_OLLAMA_API` to true and `RAG_EMBEDDING_ENGINE` to Ollama. | Ollama is explicit opt-in and default-off in every mode/package path; no health or model probe occurs while disabled. | Compose matrix and runtime mock assert disabled defaults, opt-in behavior, and zero disabled probes. |
| F11 | Endpoint normalization appends `/v1` unless the final stored segment is a numeric `vN`; operation suffix removal exists but custom-prefix behavior is not comprehensively certified. | A single operation-URL builder normalizes origins, `/v1`, operation URLs, trailing slashes, and custom prefixes without duplicating operations or rewriting an explicit custom API base. | Controlled request-path matrix for origin, `/v1`, full operation URL, trailing slash, and custom prefix. |

## Shared architectural causes

1. **Authority is inferred in consumers instead of injected once.** Storage,
   timeout, installation identity, and role-secret availability need explicit
   authorities that are selected at a boundary and passed to consumers.
2. **Certification is treated as UI session state instead of backend state.**
   A role default is a runtime trust decision and must be enforced and audited
   by Core against the exact profile configuration that was tested.
3. **Fail-open is implemented as invisibility.** Chat continuity is correct,
   but failures still need a safe durable outcome record and a truthful status
   projection.
4. **Host and container coordinates are insufficiently separated.** Installer
   port selection and Docker service discovery are different namespaces and
   must never share one mutable value.
5. **Upgrade identity lives only beside an extracted package.** Stable Docker
   resources outlive extraction directories, so credential reconciliation must
   happen before new secret generation.

## Certification record

Local and package evidence recorded on Windows with Docker Desktop:

- Core Ruff, format check, strict MyPy, and the complete pytest suite passed.
- The real PostgreSQL integration selection passed: `45 passed`, including
  capture scheduling, remote-provider orchestration, control-plane behavior,
  memory-control contracts, and trusted-actor authentication.
- Open WebUI Python integration tests passed: `53 passed`.
- Frontend Vitest passed: `49 passed`; TypeScript typecheck and ESLint passed.
- PowerShell/Pester installer tests passed on Windows PowerShell 5.1:
  `25 passed`.
- Installer static validation passed: `50 checks`; Lite and Full Compose
  validation passed.
- The rebuilt Full package started all four services healthy. A controlled
  OpenAI-compatible provider completed health certification and two live
  memory-processing operations, and certification became stale after profile
  mutation until it was retested.
- Durable fail-open evidence was verified in PostgreSQL: a capture failure
  remained visible after an unrelated successful stage and cleared only after
  capture itself recovered.
- A rebuilt Lite package started only Core and Open WebUI healthy with Ollama
  disabled and the remote embedding engine selected, avoiding implicit local
  model downloads.
- A same-project Full upgrade and a restart reused the exact PostgreSQL volume,
  retained workspace/session/message/profile data counts, and reached schema
  `22` with every service healthy.
- The generated RC contains `486` files (`485` declared payload entries) and
  has SHA-256
  `61d036d140f735acc932813578edb8041e81b4afe807a61e13d87c96becf8c50`.
- RC timestamps are deterministic and content-derived. This preserves
  byte-for-byte reproducibility while ensuring Docker Desktop observes changed
  build inputs instead of reusing a stale same-size source context.
- RC forbidden-file/local-path scanning and source-package tree scanning
  passed; no live credential, private environment file, or user-local path was
  packaged.
- No Docker volume was deleted during installation, upgrade, restart, or
  certification.

Remaining external evidence:

- GitHub Actions on the Draft PR are queued because every authoritative job
  requires `[self-hosted, linux, x64, memorist-ci]` and the repository
  currently has no registered self-hosted runner. Current-head CI must execute
  before this closure can receive a final `READY` verdict.
